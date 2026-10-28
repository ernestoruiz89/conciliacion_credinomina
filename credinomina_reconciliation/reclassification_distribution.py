"""Shared arithmetic and evidence checks for split core adjustments.

The journal preserves each use of a core movement. Callers must lock and
authorize the original item and every target before recording a use.
"""
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from contextvars import ContextVar
import json

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.rounding import decimal_value, money, sum_money

DEPOSIT = "Saldo a favor contra depósito"
COMPANY_CREDIT = "Saldo a favor de la empresa contra depósito"
INCOME = "Ingreso contra depósito"
OTHER = "Otro sin cuenta por cobrar contra depósito"
APPLICATION = "Pago pendiente contra aplicación"
DEPOSIT_KINDS = (DEPOSIT, COMPANY_CREDIT, INCOME, OTHER)
CREDIT_KINDS = (DEPOSIT, COMPANY_CREDIT)
FIELD = "reclassification_allocations"
_writer = ContextVar("cn_reclassification_distribution_writer", default=False)


@contextmanager
def journal_writer():
    token = _writer.set(True)
    try:
        yield
    finally:
        _writer.reset(token)


def allocations(item):
    raw = item.get(FIELD) or "[]"
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(entries, list):
            raise ValueError()
        ids = set()
        for entry in entries:
            if (not isinstance(entry, dict) or not entry.get("operation_id")
                    or entry.get("kind") not in (*DEPOSIT_KINDS, APPLICATION)
                    or not entry.get("target") or not entry.get("effective_date")
                    or not entry.get("parent_document") or not entry.get("employer")
                    or (entry.get("kind") in CREDIT_KINDS and not entry.get("credit_origin_date"))
                    or (entry.get("kind") == DEPOSIT and not entry.get("client"))
                    or _amount(entry.get("amount_usd")) <= 0 or entry["operation_id"] in ids):
                raise ValueError()
            ids.add(entry["operation_id"])
        return entries
    except (ValueError, TypeError, frappe.ValidationError):
        frappe.throw(_("El diario de distribuciones de la reclasificación no es válido."))


def guard_journal(item, previous=None):
    if _writer.get():
        return
    if (item.get('reclassification_deletions') or '') != ((previous or {}).get('reclassification_deletions') or ''):
        frappe.throw(_("La auditoría de distribuciones eliminadas no se puede editar."))
    current, old = allocations(item), allocations(previous or {})
    if current != old:
        frappe.throw(_("Use la acción de distribución para modificar el diario de reclasificación."))
    if old:
        for field in ("category", "docstatus", "amount", "amount_usd", "currency", "fx_rate", "employer",
                      "client_number", "client_name", "loan_number", "posting_date", "accounting_source_key",
                      "origin_deposit", "registered_deposit", "related_application", "related_import",
                      "registration_exception", "receivable_origin", "generic_distribution", "application_adjustment_usd"):
            before, after = (previous or {}).get(field), item.get(field)
            normalize = decimal_value if field in {"amount", "amount_usd", "fx_rate", "application_adjustment_usd"} else lambda value: str(value or "")
            if normalize(before) != normalize(after):
                frappe.throw(_("La partida tiene distribuciones de reclasificación; conserve su origen e identidad."))


def guard_removal(item):
    if allocations(item):
        frappe.throw(_("No se puede cancelar, eliminar ni renombrar una partida con porciones distribuidas."))


def application_links(items, wanted, *, employer=None, cutoff=None):
    """Project dated portions into the existing application-adjustment engine."""
    from frappe.utils import getdate

    result = []
    for item in items:
        for entry in allocations(item):
            if (entry["kind"] != APPLICATION or entry["target"] not in wanted
                    or (employer and entry.get("employer") != employer)
                    or (cutoff and getdate(entry["effective_date"]) > getdate(cutoff))):
                continue
            result.append(frappe._dict(name=item.get("name"), related_application=entry["target"],
                related_import=entry.get("parent_document"), application_adjustment_usd=entry["amount_usd"],
                period=entry.get("period"), adjustment_periods=entry.get("adjustment_periods"),
                adjustment_collection_rows=entry.get("adjustment_collection_rows")))
    return result


def deposit_credits(items, deposits, *, cutoff=None):
    """Project confirmed portions as cash reservations, without creating items."""
    from frappe.utils import getdate

    result = []
    for item in items:
        for entry in allocations(item):
            if (entry["kind"] not in CREDIT_KINDS or entry.get("parent_document") not in deposits
                    or (cutoff and getdate(entry.get("credit_origin_date")) > getdate(cutoff))):
                continue
            history = entry.get("management_history") or []
            if not isinstance(history, list) or any(not isinstance(row, dict) or not row.get("fecha") for row in history):
                frappe.throw(_("El historial del saldo a favor de la porción no es válido."))
            resolved = sum_money(row.get("importe_usd") for row in history
                                 if not cutoff or getdate(row["fecha"]) <= getdate(cutoff))
            pending = money(entry["amount_usd"]) - resolved
            if resolved < 0 or pending < 0:
                frappe.throw(_("El historial de la porción supera el saldo a favor disponible."))
            result.append(frappe._dict(name=item.get("name"), category=("Saldo a favor del cliente" if entry["kind"] == DEPOSIT
                else "Saldo a favor de la empresa"),
                registered_deposit=entry["parent_document"], credit_detail_row=entry["target"],
                credit_client=entry.get("client") if entry["kind"] == DEPOSIT else None, client_number=entry.get("client_number"),
                client_name=entry.get("client_name"), loan_number=entry.get("loan_number"),
                employer=entry.get("employer"), amount_usd=entry["amount_usd"],
                credit_pending_usd=float(pending), credit_resolved_usd=float(resolved),
                credit_management_status="Resuelto" if not pending else "Parcialmente resuelto" if resolved else "Pendiente",
                credit_origin_date=entry.get("credit_origin_date"), posting_date=entry.get("credit_origin_date"),
                accounting_source_key=item.get("accounting_source_key"), accounting_status="Importada del core", docstatus=1,
                result="Saldo a favor documentado", period=entry.get("period"),
                _reclassification_pending=bool(cutoff and getdate(entry["effective_date"]) > getdate(cutoff)),
                reclassification_operation_id=entry["operation_id"], reference="", deposit_voucher=""))
    return result


def load_deposit_credits(deposits):
    """Read only confirmed source items and project allocations for deposits."""
    names = set(deposits) - {None, ""}
    if not names:
        return []
    items = _source_items_for_deposits(names, ["name", FIELD, "accounting_source_key"])
    return deposit_credits(items, names)


def _source_items_for_deposits(names, fields):
    # Filter candidate journals in SQL, then validate their parsed destination
    # exactly. This avoids reading every distribution for a single deposit.
    patterns = sorted({"%" + json.dumps(name, ensure_ascii=ascii_only).replace("\\", "\\\\")
                       .replace("%", "\\%").replace("_", "\\_") + "%"
                       for name in names for ascii_only in (True, False)})
    return frappe.get_all("CN Complementary Item", filters={"docstatus": 1, FIELD: ["not in", ["", "[]"]]},
        or_filters=[[FIELD, "like", pattern] for pattern in patterns] if len(patterns) <= 100 else None,
        fields=fields, limit_page_length=0)


def load_cash_claims(deposits):
    """Expose non-credit portions as explicitly directed core cash claims."""
    names = set(deposits) - {None, ""}
    if not names:
        return []
    items = _source_items_for_deposits(names, ["name", "employer", "reference", "accounting_source_key", FIELD])
    claims = []
    for item in items:
        entries = [entry for entry in allocations(item) if entry["kind"] in (INCOME, OTHER)]
        if not entries or not any(entry["parent_document"] in names for entry in entries):
            continue
        claims.append(frappe._dict(name=item.name, employer=item.employer, reference=item.reference,
            accounting_source_key=item.accounting_source_key, amount_usd=float(sum_money(entry["amount_usd"] for entry in entries)),
            period="", client_number="", loan_number="", installment_number="", generic_distribution=1,
            receivable_origin="", category="Otros ingresos", subcategory_effect="", docstatus=1,
            _reclassification_companies=sorted({entry["employer"] for entry in entries}),
            _reclassification_entries=entries))
    return claims


def _credit_management_item(item_name, operation_id, modified):
    """Lock the source and verify access to both ends of a credit portion."""
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies

    # Follow the same lock order as link_deposit, including the company pool;
    # otherwise a simultaneous allocation and refund could deadlock.
    initial = frappe.get_doc("CN Complementary Item", item_name)
    initial.check_permission("write")
    initial.check_permission("submit")
    selected = next((row for row in allocations(initial) if row["operation_id"] == operation_id), None)
    if not selected or selected["kind"] not in CREDIT_KINDS:
        frappe.throw(_("Seleccione una porción de saldo a favor existente."))
    payer = frappe.db.get_value("CN Remittance Allocation", selected["parent_document"], "employer")
    if not payer:
        frappe.throw(_("El depósito del saldo a favor no está disponible."))
    lock_cash_pool(set(reconciliation_companies(payer)) | ({initial.employer} if initial.employer else set()))
    deposit = frappe.get_doc("CN Remittance Allocation", selected["parent_document"], for_update=True)
    deposit.check_permission("write")
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if item.docstatus != 1 or str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de registrar la gestión."))
    entries = allocations(item)
    entry = next((row for row in entries if row["operation_id"] == operation_id), None)
    if not entry or entry["kind"] not in CREDIT_KINDS:
        frappe.throw(_("Seleccione una porción de saldo a favor existente."))
    if entry["parent_document"] != deposit.name or deposit.docstatus != 1:
        frappe.throw(_("El depósito del saldo a favor debe estar confirmado."))
    if entry["kind"] == DEPOSIT:
        frappe.get_doc("CN Client", entry["client"]).check_permission("read")
    return item, entries, entry


@frappe.whitelist(methods=["POST"])
def record_credit_management(item_name, operation_id, modified, treatment, amount_usd,
                             event_date, reference, support_file="", notes=""):
    """Record external resolution of one customer or company credit portion."""
    from html import escape
    from uuid import uuid4
    from frappe.utils import getdate, now_datetime
    from credinomina_reconciliation.rounding import money_float

    item, entries, entry = _credit_management_item(item_name, operation_id, modified)
    if treatment not in {"Devolución", "Aplicación futura"} or not str(reference or "").strip() or not event_date:
        frappe.throw(_("Indique devolución o aplicación, fecha y referencia del comprobante."))
    history = entry.setdefault("management_history", [])
    if not isinstance(history, list):
        frappe.throw(_("El historial del saldo a favor de la porción no es válido."))
    # Do not insert an event before an existing correction: every historical
    # cut must retain a nonnegative, auditable balance.
    earliest = max([getdate(entry["credit_origin_date"]),
                    *[getdate(row["fecha"]) for row in history]])
    date = getdate(event_date)
    if date < earliest or date > getdate():
        frappe.throw(_("La gestión no puede ser futura ni anterior al saldo o a sus gestiones anteriores."))
    amount = _amount(amount_usd)
    resolved = sum_money(row.get("importe_usd") for row in history)
    if resolved < 0 or amount > money(entry["amount_usd"]) - resolved:
        frappe.throw(_("El importe supera el saldo a favor pendiente de esta porción."))
    support_file = str(support_file or "").strip()
    if support_file:
        name = frappe.db.get_value("File", {"file_url": support_file, "attached_to_doctype": item.doctype,
                                            "attached_to_name": item.name}, "name")
        if not name:
            frappe.throw(_("El soporte de la gestión debe estar adjunto a esta partida."))
        frappe.get_doc("File", name).check_permission("read")
    history.append(dict(operation_id=uuid4().hex, tratamiento=treatment, importe_usd=money_float(amount),
        fecha=str(date), referencia=str(reference).strip(), soporte=support_file,
        observaciones=str(notes or "").strip(), usuario=frappe.session.user, registrado_el=str(now_datetime())))
    with journal_writer():
        frappe.db.set_value(item.doctype, item.name, FIELD, frappe.as_json(entries))
    item.add_comment("Info", _("Se documentó una gestión de saldo a favor de una distribución."))
    item.clear_cache()
    return {"pending_usd": money_float(money(entry["amount_usd"]) - resolved - amount)}


@frappe.whitelist(methods=["POST"])
def reverse_credit_management(item_name, operation_id, modified, entry_id, event_date, reason):
    """Reverse an external management event while preserving the cash allocation."""
    from html import escape
    from uuid import uuid4
    from frappe.utils import getdate, now_datetime
    from credinomina_reconciliation.rounding import money_float

    item, entries, entry = _credit_management_item(item_name, operation_id, modified)
    history = entry.get("management_history") or []
    original = next((row for row in history if row.get("operation_id") == entry_id), None)
    if (not original or original.get("reverses") or money(original.get("importe_usd")) <= 0
            or any(row.get("reverses") == entry_id for row in history)):
        frappe.throw(_("La gestión no existe o ya fue revertida."))
    if not str(reason or "").strip() or not event_date:
        frappe.throw(_("Indique fecha y motivo de la reversión."))
    date = getdate(event_date)
    earliest = max([getdate(entry["credit_origin_date"]), *[getdate(row["fecha"]) for row in history]])
    if date < earliest or date > getdate():
        frappe.throw(_("La reversión no puede ser futura ni anterior a las gestiones registradas."))
    amount = money(original["importe_usd"])
    resolved = sum_money(row.get("importe_usd") for row in history)
    if resolved < amount or resolved > money(entry["amount_usd"]):
        frappe.throw(_("El saldo gestionado no coincide con el historial de esta porción."))
    history.append(dict(operation_id=uuid4().hex, reverses=entry_id, tratamiento="Reversión de gestión",
        importe_usd=-money_float(amount), fecha=str(date), referencia=original.get("referencia") or "",
        soporte=original.get("soporte") or "", observaciones=str(reason).strip(),
        usuario=frappe.session.user, registrado_el=str(now_datetime())))
    entry["management_history"] = history
    with journal_writer():
        frappe.db.set_value(item.doctype, item.name, FIELD, frappe.as_json(entries))
    item.add_comment("Info", _("Se revirtió una gestión de saldo a favor de una distribución."))
    item.clear_cache()
    return {"pending_usd": money_float(money(entry["amount_usd"]) - resolved + amount)}


def _amount(value):
    try:
        result = Decimal(str(value))
        valid = result.is_finite() and result > 0 and result == result.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw(_("Indique un importe válido en US$."))
    if not valid:
        frappe.throw(_("Indique un importe positivo con un máximo de dos decimales."))
    return result


@frappe.whitelist()
def distribution_history(item_name):
    """Return the journal only when the source and every target are readable."""
    from credinomina_reconciliation.report_records import _require_reclassification_targets
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    _require_reclassification_targets(item)
    entries = allocations(item)
    return {"modified": str(item.modified), "amount_usd": item.amount_usd,
            "remaining_usd": float(money(item.amount_usd) - sum_money(e["amount_usd"] for e in entries)),
            "entries": entries}


def identity_exception(item, target):
    """Use the same company and identity rules for the picker and the write."""
    different_company = not item.get("employer") or item.get("employer") != target.get("employer")
    item_number, target_number = (canonical_identifier(value) for value in
                                   (item.get("client_number"), target.get("client_number")))
    item_loan, target_loan = (canonical_credit_number(value) for value in
                              (item.get("loan_number"), target.get("loan_number")))
    item_name, target_name = name_key(item.get("client_name")), name_key(target.get("client_name"))
    different_client = bool((item_number and target_number and item_number != target_number)
                            or (item_loan and target_loan and item_loan != target_loan)
                            or (item_name and target_name and item_name != target_name))
    unknown_client = not ((item_number and target_number and item_number == target_number)
                          or (item_loan and target_loan and item_loan == target_loan)
                          or (item_name and target_name and item_name == target_name))
    return bool(different_company or different_client or unknown_client)


def unique_credit_updates(item, entries, deposit_references):
    """Derive missing identity only from a fully distributed, unique credit."""
    from credinomina_reconciliation.parsers import clean_text

    if not entries or sum_money(row.get("amount_usd") for row in entries) != abs(money(item.get("amount_usd"))):
        return {}
    loans = [canonical_credit_number(row.get("loan_number")) for row in entries]
    employers = {clean_text(row.get("employer")) for row in entries}
    if not all(loans) or len(set(loans)) != 1 or len(employers) != 1 or "" in employers:
        return {}
    numbers = {canonical_identifier(row.get("client_number")) for row in entries if clean_text(row.get("client_number"))}
    if len(numbers) > 1:
        return {}
    names = {name_key(row.get("client_name")) for row in entries if name_key(row.get("client_name"))}
    if not numbers and len(names) > 1:
        return {}
    existing_loan = canonical_credit_number(item.get("loan_number"))
    existing_number = canonical_identifier(item.get("client_number"))
    if ((existing_loan and existing_loan != loans[0]) or
            (existing_number and numbers and existing_number not in numbers)):
        return {}
    existing_name = name_key(item.get("client_name"))
    if not existing_number and existing_name not in ("", "0", "n/a", "na") and names and existing_name not in names:
        return {}
    changes = {}
    if not clean_text(item.get("loan_number")):
        changes["loan_number"] = clean_text(entries[0].get("loan_number"))
    if not clean_text(item.get("client_number")) and numbers:
        changes["client_number"] = next(clean_text(row.get("client_number")) for row in entries if clean_text(row.get("client_number")))
    # Reference is the bank deposit reference, not the application voucher.
    # Several deposits are safe only when all have the same nonempty reference.
    if not clean_text(item.get("reference")) and all(row.get("kind") in DEPOSIT_KINDS for row in entries):
        references = [clean_text(deposit_references.get(row.get("parent_document"))) for row in entries]
        if all(references) and len(set(references)) == 1:
            changes["reference"] = references[0]
    return changes


def apply_unique_credit_updates(item, entries):
    """Read only authorized destinations and persist the derived fields atomically."""
    if sum_money(row.get("amount_usd") for row in entries) != abs(money(item.get("amount_usd"))):
        return {}
    references = {}
    for entry in entries:
        doctype = "CN Accounting Import" if entry["kind"] == APPLICATION else "CN Remittance Allocation"
        name = entry["parent_document"]
        if not frappe.db.exists(doctype, name) or not frappe.has_permission(doctype, "read", doc=name):
            return {}  # Never infer fields from destinations the user cannot read.
        if entry.get("client") and not frappe.has_permission("CN Client", "read", doc=entry["client"]):
            return {}
        if doctype == "CN Remittance Allocation" and name not in references:
            references[name] = frappe.db.get_value(doctype, name, "deposit_reference")
    changes = unique_credit_updates(item, entries, references)
    if changes:
        frappe.db.set_value(item.doctype, item.name, changes)
        item.update(changes)
        item.add_comment("Info", _("Se completaron datos vacíos a partir de una distribución íntegra a un crédito único."))
    return changes


@frappe.whitelist()
def preview_destination(item_name, document_name, destination, override=0):
    """Read a selected target only after checking the parent document."""
    from credinomina_reconciliation.paying_employers import allowed_employers
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    if destination == "deposit":
        from credinomina_reconciliation.client_credit import load_credits
        doc = frappe.get_doc("CN Remittance Allocation", document_name)
        doc.check_permission("read")
        if doc.docstatus != 1:
            frappe.throw(_("Seleccione un depósito confirmado."))
        allowed = allowed_employers(doc.employer)
        credits = load_credits([doc.name])
        reserved = sum_money(credit.get("amount_usd") for credit in credits)
        cash_available = money(doc.amount_usd) - money(doc.allocated_usd) - reserved
        by_row = {}
        for credit in credits:
            key = credit.get("credit_detail_row")
            by_row[key] = by_row.get(key, money(0)) + money(credit.get("amount_usd"))
        rows = []
        for row in doc.detail_rows or []:
            if (not row.client or row.employer not in allowed or
                    str(row.get("identity_reason") or "").startswith(("Conflicto", "Nombre ambiguo")) or
                    not frappe.has_permission("CN Client", "read", doc=row.client)):
                continue
            available = min(money(row.pending_usd), money(row.amount_usd) - money(row.linked_usd)
                            - by_row.get(row.name, money(0)), cash_available)
            if available <= 0 or (not cint(override) and identity_exception(item, row)):
                continue
            rows.append(dict(name=row.name, employer=row.employer, client_name=row.client_name,
                client_number=row.client_number, loan_number=row.loan_number, source_row=row.source_row,
                amount_usd=row.amount_usd, pending_usd=float(available)))
        return dict(modified=str(doc.modified), rows=rows, date=str(doc.deposit_date))
    if destination == "application":
        from collections import defaultdict
        from credinomina_reconciliation.application_adjustment_links import confirmed
        from credinomina_reconciliation.application_adjustments import cash_coverage
        from credinomina_reconciliation.application_candidate_context import CandidateContext
        doc = frappe.get_doc("CN Accounting Import", document_name)
        doc.check_permission("read")
        if doc.docstatus == 2 or doc.status not in {"Importado", "Importado con excepciones"}:
            frappe.throw(_("Seleccione una importación contable vigente."))
        candidates = [row for row in doc.rows or [] if row.event_type == "Aplicacion" and row.effective
                      and row.currency == "USD" and (cint(override) or not identity_exception(item, {
                          "employer": doc.employer, "client_number": row.client_number,
                          "client_name": row.client_name, "loan_number": row.loan_number}))]
        if not candidates:
            return dict(modified=str(doc.modified), rows=[])
        context = CandidateContext(candidates, doc, item)
        totals = defaultdict(lambda: money(0))
        for link in confirmed([row.name for row in candidates], employer=doc.employer):
            totals[link.related_application] += money(link.application_adjustment_usd)
        rows = []
        for source in candidates:
            row = frappe._dict(source.as_dict())
            row.application_adjustment_usd = float(totals[row.name])
            available = money(cash_coverage(row, context=context)["adjustable_usd"])
            if available <= 0:
                continue
            rows.append(dict(name=row.name, employer=doc.employer, client_name=row.client_name,
                client_number=row.client_number, loan_number=row.loan_number, source_row=row.source_row,
                amount_usd=row.amount_usd, pending_usd=float(available),
                period=row.historical_period or row.collection_period))
        return dict(modified=str(doc.modified), rows=rows)
    frappe.throw(_("Seleccione un destino válido."))


def validate_portion(item, target, kind, amount_usd, existing, *, override=False, reason=""):
    """Check a proposed portion against authoritative locked balances.

    ``target.available_usd`` and ``existing`` must be obtained under locks by
    the caller; ``target`` must already have passed permission checks.
    """
    from credinomina_reconciliation.imported_complement_amount import ledger_magnitude

    if item.get("docstatus") != 1 or not item.get("accounting_source_key"):
        frappe.throw(_("Seleccione una partida confirmada importada del core."))
    if kind not in (*DEPOSIT_KINDS, APPLICATION):
        frappe.throw(_("Seleccione un destino de depósito o de aplicación."))
    debit, credit = money(item.get("source_debit")), money(item.get("source_credit"))
    if kind in DEPOSIT_KINDS and (debit <= 0 or credit != 0):
        frappe.throw(_("La porción contra depósito requiere un débito original del core."))
    if kind == APPLICATION and (credit <= 0 or debit != 0):
        frappe.throw(_("El ajuste contra aplicación requiere un crédito original del core."))
    original = ledger_magnitude(item)
    if original is None or original != abs(money(item.get("amount_usd"))):
        frappe.throw(_("El importe no coincide con la evidencia contable original."))
    amount = _amount(amount_usd)
    already = sum_money(entry.get("amount_usd") for entry in existing)
    if already < 0 or already + amount > original:
        frappe.throw(_("Las porciones vinculadas superan el importe de la partida del core."))
    if amount > money(target.get("available_usd")):
        frappe.throw(_("El importe supera el saldo disponible del destino."))

    exception = identity_exception(item, target)
    if exception:
        if not cint(override) or not str(reason or "").strip():
            frappe.throw(_("La empresa o el cliente no coincide. Marque la excepción y documente el motivo."))
    return {"amount_usd": money(amount), "remaining_usd": money(original - already - amount),
            "identity_exception": exception}


@frappe.whitelist(methods=["POST"])
def link_application(item_name, application_name, modified, amount_usd, *, override=False, reason=""):
    """Atomically reserve a portion against an application, without a deposit."""
    from html import escape
    from uuid import uuid4
    from frappe.utils import getdate, now_datetime
    from credinomina_reconciliation import application_adjustments
    from credinomina_reconciliation.credit_application import rebuild_destination
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.complementary_state import mark
    from credinomina_reconciliation.rounding import money_float

    parent_name = frappe.db.get_value("CN Source Row", application_name, "parent")
    company = frappe.db.get_value("CN Accounting Import", parent_name, "employer") if parent_name else None
    origin = frappe.db.get_value("CN Complementary Item", item_name, "employer")
    if not company:
        frappe.throw(_("La aplicación no tiene una empresa identificada."))
    lock_cash_pool({value for value in (origin, company) if value})
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de distribuir."))
    if (item.category != "Compensación entre partidas" or item.get("registered_deposit")
            or item.get("origin_deposit") or item.get("related_application")
            or item.get("registration_exception") or item.get("receivable_origin")
            or item.get("compensations") or money(item.get("compensated_usd"))
            or money(item.get("application_adjustment_usd")) or item.get("generic_distribution")):
        frappe.throw(_("La partida ya tiene un tratamiento financiero incompatible."))
    if frappe.db.exists("CN Remittance Target", {"complementary_item": item.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida ya está seleccionada como destino de un depósito."))
    source = frappe.get_doc("CN Source Row", application_name, for_update=True)
    if source.parent != parent_name or source.parenttype != "CN Accounting Import":
        frappe.throw(_("La aplicación seleccionada no pertenece a una importación contable."))
    parent = frappe.get_doc("CN Accounting Import", parent_name)
    parent.check_permission("write")
    if (source.event_type != "Aplicacion" or not source.effective or source.docstatus == 2
            or source.currency != "USD" or parent.docstatus == 2 or parent.employer != company
            or parent.status not in {"Importado", "Importado con excepciones"}
            or (source.get("accounting_source_key") and
                (money(source.get("source_debit")) <= 0 or money(source.get("source_credit")) != 0))):
        frappe.throw(_("Seleccione una aplicación contable efectiva de la empresa."))
    period_name = source.historical_period or source.collection_period
    if not period_name:
        frappe.throw(_("Asigne primero un período a la aplicación."))
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    if period.status == "Cerrado" or period.employer != company:
        frappe.throw(_("La aplicación debe pertenecer a un período abierto de su empresa."))
    application_adjustments.assert_adjustable(source, item.name)
    application_adjustments.refresh_rows([source], employer=company)
    coverage = application_adjustments.cash_coverage(source, lock=True)
    target = dict(employer=company, client_number=source.client_number,
                  client_name=source.client_name, loan_number=source.loan_number,
                  available_usd=coverage["adjustable_usd"])
    existing = allocations(item)
    proposed = validate_portion(item, target, APPLICATION, amount_usd, existing,
                                override=override, reason=reason)
    origin_date = item.get("source_date") or item.posting_date
    if not origin_date or not source.event_date:
        frappe.throw(_("La partida y la aplicación deben tener una fecha contable."))
    effective = max(getdate(origin_date), getdate(source.event_date))
    if effective > getdate():
        frappe.throw(_("La fecha efectiva no puede ser futura."))
    client_number = source.client_number or ""
    client_id = source.get("client") or source.get("portfolio_client")
    if (not client_number and client_id and frappe.db.exists("CN Client", client_id)
            and frappe.has_permission("CN Client", "read", doc=client_id)):
        client_number = frappe.db.get_value("CN Client", client_id, "client_number") or ""
    entry = dict(operation_id=uuid4().hex, kind=APPLICATION, target=source.name,
        parent_document=parent.name, period=period.name, employer=company,
        client_number=client_number, client_name=source.client_name or "",
        loan_number=source.loan_number or "", amount_usd=money_float(proposed["amount_usd"]),
        effective_date=str(effective), identity_exception=int(proposed["identity_exception"]),
        reason=str(reason or "").strip(), recorded_by=frappe.session.user, recorded_on=str(now_datetime()))
    frappe.db.savepoint("cn_split_application")
    try:
        with journal_writer():
            frappe.db.set_value(item.doctype, item.name, FIELD, frappe.as_json([*existing, entry]))
        mark([item.name])
        rebuild_destination(period)
        application_adjustments.assert_cash_preserved(coverage["snapshots"])
        apply_unique_credit_updates(item, [*existing, entry])
        # A source can be visible to users who cannot read the target company.
        # Keep target identity and user-entered reasons in the restricted journal.
        item.add_comment("Comment", _("Se registró una distribución de la partida del core."))
        parent.add_comment("Comment", _("Se registró una distribución en una aplicación contable."))
        return {"operation_id": entry["operation_id"], "remaining_usd": money_float(proposed["remaining_usd"]),
                "application": source.name, "effective_date": entry["effective_date"]}
    except Exception:
        frappe.db.rollback(save_point="cn_split_application")
        frappe.clear_document_cache(item.doctype, item.name)
        raise


@frappe.whitelist(methods=["POST"])
def link_deposit(item_name, deposit_name, detail_name, modified, deposit_modified, amount_usd, *, kind=DEPOSIT, override=False, reason=""):
    """Atomically apply one classified portion to a deposit detail."""
    from html import escape
    from uuid import uuid4
    from frappe.utils import getdate, now_datetime
    from credinomina_reconciliation.client_credit import load_credits
    from credinomina_reconciliation.complementary_state import mark
    from credinomina_reconciliation.deposit_accounting_sources import assert_usable_cash
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool, reconcile_deposit
    from credinomina_reconciliation.paying_employers import allowed_employers, reconciliation_companies
    from credinomina_reconciliation.rounding import money_float

    if kind not in DEPOSIT_KINDS:
        frappe.throw(_("Seleccione un tratamiento válido para el depósito."))
    if not str(reason or "").strip():
        frappe.throw(_("Documente el motivo de la distribución."))

    payer = frappe.db.get_value("CN Remittance Allocation", deposit_name, "employer")
    origin = frappe.db.get_value("CN Complementary Item", item_name, "employer")
    if not payer:
        frappe.throw(_("El depósito no tiene empresa identificada."))
    lock_cash_pool(set(reconciliation_companies(payer)) | ({origin} if origin else set()))
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name, for_update=True)
    deposit.check_permission("write")
    assert_usable_cash(deposit)
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if str(item.modified) != str(modified) or str(deposit.modified) != str(deposit_modified):
        frappe.throw(_("El depósito o la partida cambió. Recargue antes de distribuir."))
    if (item.category != "Compensación entre partidas" or item.get("registered_deposit")
            or item.get("origin_deposit") or item.get("related_application")
            or item.get("registration_exception") or item.get("receivable_origin") or item.get("compensations")
            or money(item.get("compensated_usd")) or money(item.get("application_adjustment_usd"))
            or item.get("generic_distribution")):
        frappe.throw(_("La partida ya tiene un tratamiento financiero incompatible."))
    if frappe.db.exists("CN Remittance Target", {"complementary_item": item.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida ya está seleccionada como destino de un depósito."))
    if deposit.docstatus != 1:
        frappe.throw(_("Seleccione un depósito confirmado."))
    row = next((row for row in deposit.detail_rows or [] if row.name == detail_name), None)
    if (not row or not row.client or not row.employer or row.employer not in allowed_employers(payer)
            or str(row.get("identity_reason") or "").startswith(("Conflicto", "Nombre ambiguo"))):
        frappe.throw(_("Seleccione una fila de cliente identificada y autorizada para el depósito."))
    client = frappe.get_doc("CN Client", row.client)
    client.check_permission("read")
    deposit._assert_open_related_periods()
    credits = load_credits([deposit.name])
    reserved = sum_money(credit.amount_usd for credit in credits)
    reserved_row = sum_money(credit.amount_usd for credit in credits if credit.credit_detail_row == row.name)
    row_available = money(row.amount_usd) - money(row.linked_usd) - reserved_row
    cash_available = money(deposit.amount_usd) - money(deposit.allocated_usd) - reserved
    target = dict(employer=row.employer, client_number=row.client_number, client_name=row.client_name,
                  loan_number=row.loan_number, available_usd=min(row_available, cash_available))
    existing = allocations(item)
    proposed = validate_portion(item, target, kind, amount_usd, existing, override=override, reason=reason)
    origin_date = item.get("source_date") or item.posting_date
    if not origin_date or not deposit.deposit_date:
        frappe.throw(_("La partida y el depósito deben tener una fecha contable."))
    effective = max(getdate(origin_date), getdate(deposit.deposit_date))
    if effective > getdate():
        frappe.throw(_("La fecha efectiva no puede ser futura."))
    entry = dict(operation_id=uuid4().hex, kind=kind, target=row.name, parent_document=deposit.name,
        employer=row.employer, client=row.client, client_number=row.client_number or client.client_number or "",
        client_name=row.client_name or "", loan_number=row.loan_number or "",
        amount_usd=money_float(proposed["amount_usd"]), credit_origin_date=str(getdate(deposit.deposit_date)),
        effective_date=str(effective), identity_exception=int(proposed["identity_exception"]),
        reason=str(reason or "").strip(), recorded_by=frappe.session.user, recorded_on=str(now_datetime()))
    frappe.db.savepoint("cn_split_deposit")
    try:
        field = ("client_credit_usd" if kind == DEPOSIT else "company_credit_usd"
                 if kind == COMPANY_CREDIT else "linked_usd")
        before_credit = money(row.get(field))
        with journal_writer():
            frappe.db.set_value(item.doctype, item.name, FIELD, frappe.as_json([*existing, entry]))
        mark([item.name], deposits=[deposit.name])
        reconcile_deposit(deposit)
        row.reload()
        if money(row.get(field)) != before_credit + proposed["amount_usd"] or money(row.pending_usd) < 0:
            frappe.throw(_("La porción no pudo registrarse sin alterar el saldo de la fila."))
        apply_unique_credit_updates(item, [*existing, entry])
        item.add_comment("Comment", _("Se registró una distribución de la partida del core."))
        deposit.add_comment("Comment", _("Se registró una distribución en el detalle del depósito."))
        return {"operation_id": entry["operation_id"], "remaining_usd": money_float(proposed["remaining_usd"]),
                "deposit": deposit.name, "detail_row": row.name, "credit_origin_date": entry["credit_origin_date"],
                "effective_date": entry["effective_date"]}
    except Exception:
        frappe.db.rollback(save_point="cn_split_deposit")
        frappe.clear_document_cache(item.doctype, item.name)
        frappe.clear_document_cache(deposit.doctype, deposit.name)
        raise
