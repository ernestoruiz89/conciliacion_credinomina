"""Accounting follow-up without posting to the core or changing cash coverage."""
from contextvars import ContextVar
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from credinomina_reconciliation.rounding import decimal_value, money, money_float

ITEM = "CN Complementary Item"
EXCEPTION = "CN Reconciliation Exception"
IMPORT = "CN Accounting Import"
SOURCE = "CN Source Row"
ACTION = "Registrar ajuste en el core"
_verifying = ContextVar("cn_verifying_core_registration", default=False)
PROOF_FIELDS = (
    "core_evidence_type", "core_evidence_name", "core_evidence_key",
    "core_evidence_fingerprint", "core_verified_on", "core_verified_by",
    "core_verification_summary",
)


def _item(name):
    item = frappe.get_doc(ITEM, name)
    item.check_permission("read")
    if item.docstatus == 2 or (item.category == "Diferencia por tolerancia" and item.status != "Vigente"):
        frappe.throw(_("La partida está cancelada o el ajuste de tolerancia ya no está vigente."))
    if not money(item.amount_usd):
        frappe.throw(_("La partida debe tener un importe distinto de cero."))
    return item


@frappe.whitelist()
def get_item_exception(item_name):
    _item(item_name)
    name = frappe.db.get_value(EXCEPTION, {"complementary_item": item_name}, "name")
    if name:
        frappe.get_doc(EXCEPTION, name).check_permission("read")
    return name


@frappe.whitelist()
def create_item_exception(item_name, assigned_to, commitment_date):
    item = _item(item_name)
    if item.get('receivable_origin'):
        frappe.throw(_('El cobro de CxC conserva la evidencia del depósito o compensación. Gestione el registro contable desde la partida original.'))
    # Serialize double clicks; the unique Link is also a database constraint.
    frappe.db.get_value(ITEM, item.name, "name", for_update=True)
    existing = get_item_exception(item.name)
    if existing:
        return existing
    if not frappe.has_permission(EXCEPTION, "create"):
        frappe.throw(_("No tiene permiso para crear excepciones."), frappe.PermissionError)
    if not assigned_to or not commitment_date:
        frappe.throw(_("Indique responsable y fecha compromiso."))
    document = frappe.get_doc({
        "doctype": EXCEPTION, "complementary_item": item.name,
        "status": "En revision", "exception_type": "Registro contable pendiente",
        "cause_category": "Registro contable pendiente", "assigned_to": assigned_to,
        "commitment_date": commitment_date, "next_action": ACTION,
        "core_voucher": item.get("source_voucher") or item.voucher or "",
        "description": _("Registrar y verificar en la importación contable el ajuste de la partida {0}. {1}").format(
            item.name, item.description or ""),
    })
    document.append("follow_up_actions", {"action_type": "Ajuste", "details": ACTION})
    document.insert()
    return document.name


def validate_exception(doc, previous=None):
    origin = doc.get("complementary_item")
    if previous and previous.get("complementary_item") != origin and previous.get("complementary_item"):
        frappe.throw(_("No se puede cambiar ni retirar la partida de origen de la excepción."))
    changed_proof = any(
        str(doc.get(field) or "") != str((previous.get(field) if previous else None) or "")
        for field in PROOF_FIELDS
    )
    if changed_proof and not _verifying.get():
        frappe.throw(_("Use Verificar asiento y resolver para registrar la verificación contable."))
    if not origin:
        if any(doc.get(field) for field in PROOF_FIELDS):
            frappe.throw(_("La verificación requiere una partida de origen."))
        return
    item = _item(origin)
    if doc.get("period") or doc.get("collection_row_id") or doc.get("source_import") or doc.get("related_case_id") or doc.get("exception_key"):
        frappe.throw(_("El seguimiento contable se vincula a la partida, no a una cobranza o aplicación. El período de origen es informativo."))
    # No mutation of a closed period: this is a separate accounting task, not a
    # financial exception counted by period closure/reconciliation.
    doc.origin_period = item.period or ""
    doc.employer = item.employer or ""
    doc.client_number = item.client_number or ""
    doc.loan_number = item.loan_number or ""
    doc.client_name = item.get("source_client_name") or ""
    doc.amount_usd = money_float(item.amount_usd)
    doc.amount_nio = money_float(item.amount) if item.currency == "NIO" else 0
    if not doc.assigned_to or not doc.commitment_date or not doc.next_action:
        frappe.throw(_("Indique responsable, fecha compromiso y próxima gestión para el registro en el core."))
    if doc.status == "Descartada":
        frappe.throw(_("Una gestión de registro contable debe verificarse y resolverse; no puede descartarse sin verificar."))
    if previous and previous.get("core_evidence_key") and doc.core_voucher != previous.core_voucher:
        frappe.throw(_("El asiento verificado no se puede modificar."))
    if doc.get("core_evidence_key"):
        evidence = _evidence(doc.core_evidence_type, doc.core_evidence_name, item, doc.core_voucher)
        if evidence["key"] != doc.core_evidence_key or evidence["fingerprint"] != doc.core_evidence_fingerprint:
            frappe.throw(_("La evidencia importada cambió. Revise el registro contable antes de resolver."))
    elif doc.status == "Resuelta":
        frappe.throw(_("Documentar el asiento no es suficiente: verifíquelo contra la importación contable antes de resolver."))


def sync_item_registration(doc):
    if not doc.get("complementary_item"):
        return
    from credinomina_reconciliation.accounting_registration import exception_registration_status
    frappe.db.set_value(ITEM, doc.complementary_item, {
        "accounting_exception": doc.name,
        "accounting_status": exception_registration_status(doc),
    }, update_modified=False)
    frappe.clear_document_cache(ITEM, doc.complementary_item)


def apply_registration_status(item):
    """Preserve explicit follow-up even when an automatic tolerance is rebuilt."""
    from credinomina_reconciliation.accounting_registration import base_registration_status, exception_registration_status
    item.accounting_status = base_registration_status(item)
    if not item.get("accounting_exception"):
        return
    exception = frappe.db.get_value(EXCEPTION, item.accounting_exception,
        ["complementary_item", "status", "core_evidence_key", "core_voucher"], as_dict=True)
    if not exception or exception.complementary_item != item.name:
        frappe.throw(_("El vínculo de seguimiento contable de la partida no es válido."))
    item.accounting_status = exception_registration_status(exception)


def guard_item_link(item, previous=None):
    for field in ("accounting_exception", "registration_exception"):
        if previous and item.get(field) != previous.get(field):
            frappe.throw(_("El vínculo de excepción se administra desde Crear / Ver excepción."))
        if not previous and item.get(field):
            frappe.throw(_("Cree la excepción desde la partida guardada."))
    if previous and previous.get("accounting_exception"):
        proof = frappe.db.get_value(EXCEPTION, previous.accounting_exception, "core_evidence_key")
        if proof and any(str(item.get(field) or "") != str(previous.get(field) or "")
                         for field in ("amount", "currency", "fx_rate", "employer", "client_number", "loan_number", "generic_distribution")):
            frappe.throw(_("No se puede cambiar el importe o identidad de una partida con asiento verificado."))
        if proof and _companies(item) != _companies(previous):
            frappe.throw(_("No se pueden cambiar las empresas autorizadas de una partida con asiento verificado."))
    if previous and previous.get("registration_exception") and any(
        (item.get(field) or "") != (previous.get(field) or "") for field in ("employer", "client_number", "loan_number")
    ):
        frappe.throw(_("No se puede cambiar la identidad de un asiento que verifica una excepción."))


def _companies(item):
    return {name for name in [item.employer, *[
        row.employer for row in (item.get("distribution_companies") or []) if item.get("generic_distribution")
    ]] if name and name != "NO IDENTIFICADA"}


def _evidence(kind, name, item, voucher):
    from credinomina_reconciliation.client_credit import CREDIT_CATEGORIES
    from credinomina_reconciliation.complementary_compensation import balance
    if item.category in CREDIT_CATEGORIES and money(balance(item)["compensated_usd"]):
        frappe.throw(_("El saldo a favor ya tiene evidencia vinculada en Compensar con otra partida. No verifique el mismo saldo nuevamente como registro contable."))
    if kind not in {SOURCE, ITEM} or not name or not (voucher or "").strip():
        frappe.throw(_("Indique el asiento y seleccione un movimiento contable importado."))
    row = frappe.get_doc(kind, name)
    parent = None
    if kind == SOURCE:
        if row.parenttype != IMPORT or row.parentfield != "rows":
            frappe.throw(_("El movimiento no pertenece a una importación contable."))
        parent = frappe.get_doc(IMPORT, row.parent)
        parent.check_permission("read")
        if parent.docstatus == 2 or parent.status not in {"Importado", "Importado con excepciones"}:
            frappe.throw(_("La importación contable debe estar cargada y vigente."))
        if row.event_type != "Ajuste":
            frappe.throw(_("Seleccione el registro del ajuste, no una aplicación de pago ni un depósito."))
        employer, actual_voucher = parent.employer, row.voucher
        if row.complementary_item:
            _check_evidence_item(frappe.get_doc(ITEM, row.complementary_item), item)
    else:
        row.check_permission("read")
        _check_evidence_item(row, item)
        employer, actual_voucher = row.employer, row.source_voucher
    if employer not in _companies(item):
        frappe.throw(_("El asiento importado debe pertenecer a la empresa de la partida o a una empresa autorizada para su distribución."))
    if (actual_voucher or "").strip() != voucher.strip():
        frappe.throw(_("El asiento indicado no coincide con la evidencia original importada."))
    key = row.get("accounting_source_key")
    source_file = row.get("source_file") or (parent and parent.source_file)
    if not key or not row.source_account or not source_file:
        frappe.throw(_("El movimiento no tiene evidencia contable original. Importe el archivo del core."))
    debit, credit = money(row.source_debit), money(row.source_credit)
    if debit < 0 or credit < 0 or bool(debit) == bool(credit):
        frappe.throw(_("Seleccione una línea contable con un débito o un crédito original distinto de cero."))
    amount = debit - credit
    currency = row.source_currency
    rate = row.get("source_fx_rate") or (parent and parent.manual_fx_rate) or 0
    if currency == "NIO" and decimal_value(rate) > 0:
        amount = money(amount / decimal_value(rate))
    elif currency != "USD":
        frappe.throw(_("La evidencia debe estar en USD o NIO con tasa válida para convertir a USD."))
    if amount != money(item.amount_usd):
        frappe.throw(_("El signo e importe neto del asiento ({0} US$) no coinciden con la partida ({1} US$).").format(amount, money(item.amount_usd)))
    for field in ("client_number", "loan_number"):
        if item.get(field) and row.get(field) and item.get(field) != row.get(field):
            frappe.throw(_("El cliente o crédito del asiento no coincide con la partida."))
    values = {"type": kind, "name": name, "key": key, "employer": employer,
        "voucher": actual_voucher, "amount_usd": money_float(amount), "currency": currency,
        "original_amount": money_float(debit - credit), "rate": str(rate),
        "source_account": row.source_account, "source_file": source_file,
        "source_date": str(row.get("source_date") or row.get("event_date") or ""),
        "description": row.get("source_description") or "", "import": parent.name if parent else "",
        "source_row": row.source_row, "item_amount_usd": str(money(item.amount_usd))}
    # Human-readable parent names may change without changing the ledger line.
    fingerprint = {key: value for key, value in values.items() if key not in {"type", "name", "import"}}
    values["fingerprint"] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str).encode()).hexdigest()
    return values


def _check_evidence_item(row, origin):
    row.check_permission("read")
    if row.name == origin.name and row.accounting_source_key:
        return
    # Imported drafts are evidence of a real core posting, not a second claim.
    if row.docstatus != 0 or row.related_application or row.compensations or row.get("registered_deposit"):
        frappe.throw(_("El asiento ya tiene un uso conciliatorio. No puede registrar dos veces el mismo ajuste."))
    if row.review_action not in {"Pendiente de revisión", "No conciliatoria"} or row.category not in {"Por clasificar", "Ajuste de conciliación"}:
        frappe.throw(_("El asiento debe ser evidencia pendiente de revisión o no conciliatoria, sin otros efectos."))


@frappe.whitelist()
def get_registration_candidates(exception_name, voucher):
    document = frappe.get_doc(EXCEPTION, exception_name)
    document.check_permission("write")
    item = _item(document.complementary_item)
    if not (voucher or "").strip():
        frappe.throw(_("Indique el asiento contable que desea verificar."))
    companies = list(_companies(item))
    imports = frappe.get_list(IMPORT, filters={"employer": ["in", companies],
        "status": ["in", ["Importado", "Importado con excepciones"]]}, pluck="name", limit_page_length=0)
    references = []
    # Chunk parent filters, but search an exact voucher, never load all ledger rows.
    for start in range(0, len(imports), 300):
        references.extend((SOURCE, row) for row in frappe.get_all(SOURCE,
            filters={"parent": ["in", imports[start:start + 300]], "parenttype": IMPORT,
                "parentfield": "rows", "voucher": voucher.strip(), "event_type": "Ajuste"}, pluck="name", limit_page_length=0))
    references.extend((ITEM, row) for row in frappe.get_list(ITEM,
        filters={"employer": ["in", companies], "source_voucher": voucher.strip(),
            "accounting_source_key": ["is", "set"]}, pluck="name", limit_page_length=0))
    result, seen = [], set()
    for kind, name in references:
        muted = frappe.flags.mute_messages
        frappe.flags.mute_messages = True
        try:
            evidence = _evidence(kind, name, item, voucher)
        except (frappe.ValidationError, frappe.PermissionError):
            continue
        finally:
            frappe.flags.mute_messages = muted
        used = frappe.db.get_value(EXCEPTION, {"core_evidence_key": evidence["key"]}, "name")
        if evidence["key"] in seen or (used and used != document.name):
            continue
        seen.add(evidence["key"])
        result.append({key: evidence[key] for key in (
            "type", "name", "voucher", "employer", "source_date", "original_amount",
            "currency", "amount_usd", "source_row", "import", "description")})
    return result


@frappe.whitelist()
def verify_and_resolve(exception_name, voucher, evidence_type, evidence_name, resolution):
    frappe.db.get_value(EXCEPTION, exception_name, "name", for_update=True)
    document = frappe.get_doc(EXCEPTION, exception_name)
    document.check_permission("write")
    if document.status == "Resuelta" or document.get("core_evidence_key"):
        frappe.throw(_("Esta excepción ya tiene un asiento verificado."))
    if not (resolution or "").strip():
        frappe.throw(_("Escriba la resolución de la gestión."))
    item = _item(document.complementary_item)
    if evidence_type not in {SOURCE, ITEM}:
        frappe.throw(_("Seleccione evidencia contable importada válida."))
    frappe.db.get_value(evidence_type, evidence_name, "name", for_update=True)
    evidence = _evidence(evidence_type, evidence_name, item, voucher)
    used = frappe.db.get_value(EXCEPTION, {"core_evidence_key": evidence["key"]}, "name", for_update=True)
    if used and used != document.name:
        frappe.throw(_("Este movimiento ya verifica otra excepción: {0}.").format(used))
    # Keep the imported adjustment in the turnover report, but never submit it
    # again as a new cash claim. The original debit/credit evidence is untouched.
    row = frappe.get_doc(evidence_type, evidence_name)
    evidence_item = row if evidence_type == ITEM else (
        frappe.get_doc(ITEM, row.complementary_item) if row.complementary_item else None)
    if evidence_item and evidence_item.name != item.name:
        evidence_item.check_permission("write")
        evidence_item.review_action = "No conciliatoria"
        evidence_item.review_notes = _("Registro en el core de {0}; verificado en {1}. No distribuir nuevamente.").format(item.name, document.name)
        evidence_item.save()
    document.core_voucher = voucher.strip()
    document.core_evidence_type = evidence_type
    document.core_evidence_name = evidence_name
    document.core_evidence_key = evidence["key"]
    document.core_evidence_fingerprint = evidence["fingerprint"]
    document.core_verified_on = now_datetime()
    document.core_verified_by = frappe.session.user
    document.core_verification_summary = _("Asiento {0} · {1} · Fila {2} · {3} {4} · Neto US$ {5} · Empresa: {6}").format(
        evidence["voucher"], evidence["import"] or evidence_name, evidence["source_row"],
        evidence["currency"], evidence["original_amount"], evidence["amount_usd"], evidence["employer"])
    document.status = "Resuelta"
    document.resolution = resolution.strip()
    document.append("follow_up_actions", {"action_type": "Soporte recibido",
        "details": document.core_verification_summary + "\n" + document.resolution,
        "external_reference": document.core_voucher})
    token = _verifying.set(True)
    try:
        document.save()
    finally:
        _verifying.reset(token)
    if evidence_item and evidence_item.name != item.name:
        frappe.db.set_value(ITEM, evidence_item.name, "registration_exception", document.name, update_modified=False)
        frappe.clear_document_cache(ITEM, evidence_item.name)
    return {"name": document.name, "status": document.status, "summary": document.core_verification_summary}


def guard_registered_evidence(doc):
    if not doc.get("registration_exception"):
        return
    exception = frappe.db.get_value(EXCEPTION, doc.registration_exception,
        ["name", "complementary_item"], as_dict=True)
    if not exception or (
        doc.docstatus != 0 or doc.review_action != "No conciliatoria" or doc.related_application or doc.compensations
    ):
        frappe.throw(_("Este movimiento verifica el registro contable de {0}; no puede volver a distribuirse ni compensarse.").format(doc.registration_exception))


def guard_verified_import(doc, previous, evidence_fields):
    """A verified physical line cannot disappear during an individual reimport."""
    if not previous:
        return
    old = {row.name: row for row in previous.rows or []}
    current = {row.name: row for row in doc.rows or []}
    names = list(old)
    for start in range(0, len(names), 500):
        verified = frappe.get_all(EXCEPTION, filters={"core_evidence_type": SOURCE,
            "core_evidence_name": ["in", names[start:start + 500]]}, pluck="core_evidence_name")
        for name in verified:
            if name not in current or any(
                str(current[name].get(field) or "") != str(old[name].get(field) or "")
                for field in evidence_fields
            ) or any(
                str(doc.get(field) or "") != str(previous.get(field) or "")
                for field in ("employer", "source_file", "currency", "manual_fx_rate")
            ):
                frappe.throw(_("Esta importación contiene un asiento verificado en una excepción. No borre ni reprocese su evidencia; cargue las correcciones del core como movimientos nuevos."))


def guard_item_delete(item):
    if item.get("accounting_exception") or item.get("registration_exception"):
        frappe.throw(_("La partida tiene seguimiento contable. Conserve el registro y su evidencia."))


@frappe.whitelist()
def get_verified_document(exception_name):
    document = frappe.get_doc(EXCEPTION, exception_name)
    document.check_permission("read")
    if document.core_evidence_type not in {ITEM, SOURCE} or not document.core_evidence_name:
        frappe.throw(_("La excepción todavía no tiene evidencia verificada."))
    row = frappe.get_doc(document.core_evidence_type, document.core_evidence_name)
    if document.core_evidence_type == SOURCE:
        row = frappe.get_doc(IMPORT, row.parent)
    row.check_permission("read")
    return {"doctype": row.doctype, "name": row.name}
