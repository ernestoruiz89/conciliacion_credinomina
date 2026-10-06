"""Dated, paired offsets of accounting evidence; never cash or loan payments."""
import re
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import getdate, get_datetime, nowdate, now_datetime

from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money
from credinomina_reconciliation.client_credit import CREDIT_CATEGORIES, RESULT as CREDIT_RESULT
from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.parsers import canonical_identifier, clean_text, normalize_credit_number

CATEGORY = "Compensación entre partidas"
DOCTYPE = "CN Complementary Item"
_WRITE_TOKEN = object()
_ENTRY_FIELDS = ("name", "usd_currency", "operation_id", "counterpart", "compensation_date", "amount_usd", "reason", "confirmed_by", "confirmed_on", "entry_key", "reverses_operation_id")
_FROZEN_FIELDS = ("category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "period",
                  "related_application", "related_import", "registered_deposit", "client_number", "loan_number", "description", "reference",
                  "credit_client", "credit_detail_row")


def balance(doc, as_of_date=None):
    cutoff = getdate(as_of_date or nowdate())
    original = abs(money(doc.get("amount_usd"))) if getdate(doc.posting_date) <= cutoff else money(0)
    compensated = sum_money(row.amount_usd for row in doc.get("compensations", [])
                            if getdate(row.compensation_date) <= cutoff)
    pending = original - compensated
    return {"original_usd": money_float(original), "compensated_usd": money_float(compensated),
            "pending_usd": money_float(pending), "status": "Compensada totalmente" if compensated and not pending
            else "Compensada parcialmente" if compensated else "Sin compensar"}


def guard_document(doc, previous=None):
    """Readonly in the UI is not a security boundary; ledger writes are server-only."""
    if doc.flags.get("compensation_write_token") is _WRITE_TOKEN:
        return
    old = previous.get("compensations", []) if previous else []
    current = doc.get("compensations", [])
    def normalized(field, value):
        if field in {"amount", "amount_usd", "fx_rate"}:
            return decimal_value(value)
        if value and field in {"posting_date", "compensation_date"}:
            return getdate(value)
        if value and field == "confirmed_on":
            return get_datetime(value)
        return str(value or "")
    def signature(rows):
        return [tuple(normalized(field, row.get(field)) for field in _ENTRY_FIELDS) for row in rows]
    if signature(old) != signature(current):
        frappe.throw(_("Use Compensar con otra partida. El historial no puede editarse manualmente."))
    if old:
        for field in _FROZEN_FIELDS:
            if normalized(field, doc.get(field)) != normalized(field, previous.get(field)):
                frappe.throw(_("No se puede cambiar {0}: la partida tiene compensaciones confirmadas.").format(field))
        if doc.docstatus != previous.docstatus:
            frappe.throw(_("Una partida compensada no se envía ni se cancela como partida de depósito."))


def update_totals(doc):
    if doc.category == CATEGORY:
        if doc.docstatus != 0 and not doc.get("compensations"):
            frappe.throw(_("Confirme desde Compensar con otra partida; no use Enviar para esta categoría."))
        if doc.related_application or doc.registered_deposit:
            frappe.throw(_("La compensación entre partidas no puede vincularse a una aplicación ni a un depósito."))
        doc.review_action = CATEGORY
    values = balance(doc) if doc.get("compensations") or doc.category == CATEGORY else None
    doc.compensated_usd = values["compensated_usd"] if values else 0
    doc.compensation_pending_usd = values["pending_usd"] if values else 0
    doc.compensation_status = values["status"] if values else ""
    if values and doc.category not in CREDIT_CATEGORIES:
        doc.review_status = values["status"]


def guard_delete(doc):
    if doc.get("compensations"):
        frappe.throw(_("No se puede eliminar, cancelar o renombrar una partida con compensaciones confirmadas."))


def _eligible(doc):
    if doc.get("registration_exception"):
        frappe.throw(_("La partida ya verifica un registro contable. No puede compensarse también."))
    if doc.category in CREDIT_CATEGORIES:
        if (doc.docstatus != 1 or doc.get("result") != CREDIT_RESULT or not doc.get("registered_deposit")
                or money(doc.amount_usd) <= 0 or doc.get("related_application")):
            frappe.throw(_("Seleccione un saldo a favor confirmado y documentado en un depósito."))
        if doc.get("accounting_exception") and frappe.db.get_value(
            "CN Reconciliation Exception", doc.accounting_exception, "core_evidence_key"
        ):
            frappe.throw(_("El saldo a favor ya tiene un asiento verificado en su excepción; no lo compense nuevamente."))
        # This journal links accounting evidence only. The original deposit,
        # closed collection periods and refund-management balance stay intact.
        return
    if doc.docstatus == 2 or (doc.docstatus == 1 and doc.category != CATEGORY) or doc.category in {"Ajuste de aplicación", "Diferencia por tolerancia"}:
        frappe.throw(_("Seleccione un borrador, una partida en compensación o un saldo a favor confirmado; no ajustes de aplicación ni tolerancias."))
    if doc.related_application or doc.registered_deposit or doc.review_action in {"Ajuste de aplicación", "Partida de depósito", "Reversión identificada"}:
        frappe.throw(_("La partida está destinada a una aplicación o depósito; revise ese vínculo antes de compensarla."))
    if doc.period and frappe.db.get_value("CN Reconciliation Period", doc.period, "status") == "Cerrado":
        frappe.throw(_("La partida pertenece a un período cerrado. Reábralo antes de compensar."))
    if frappe.db.exists("CN Remittance Target", {"complementary_item": doc.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida está seleccionada en un depósito y no puede compensarse también."))


def _direction(doc):
    if doc.category in CREDIT_CATEGORIES:
        # Stored as a positive magnitude, but represents excess credited to the
        # customer/company; only an original core debit can offset that excess.
        return -1
    # Imports store magnitudes: derive direction from untouched original evidence.
    if doc.get("accounting_source_key"):
        debit, credit = money(doc.source_debit), money(doc.source_credit)
        if debit > 0 and not credit:
            return 1
        if credit > 0 and not debit:
            return -1
        frappe.throw(_("La evidencia debe identificar un débito o crédito único para compensar la partida."))
    amount = money(doc.amount_usd)
    return 1 if amount > 0 else -1 if amount < 0 else 0


def _validate_pair(left, right):
    if left.name == right.name:
        frappe.throw(_("Seleccione una partida diferente."))
    for doc in (left, right):
        _eligible(doc)
    credits = [doc for doc in (left, right) if doc.category in CREDIT_CATEGORIES]
    if credits:
        if len(credits) != 1:
            frappe.throw(_("Vincule el saldo a favor con un movimiento contable, no con otro saldo a favor."))
        credit = credits[0]
        _validate_credit_evidence(credit, right if credit.name == left.name else left)
    if left.employer and right.employer and left.employer != right.employer:
        frappe.throw(_("Las partidas pertenecen a empresas diferentes."))
    if left.source_account and right.source_account and left.source_account != right.source_account:
        frappe.throw(_("Las partidas corresponden a cuentas contables diferentes."))
    if not _direction(left) or _direction(left) == _direction(right):
        frappe.throw(_("Las partidas deben tener sentidos opuestos: débito y crédito, o importes manuales de signo contrario."))


def _validate_credit_evidence(credit, evidence):
    """An imported debit can explain excess cash, never consume it a second time."""
    from credinomina_reconciliation.accounting_registration import base_registration_status
    from credinomina_reconciliation.accounting_types import APPLICATION, DEBIT_NOTE
    if (base_registration_status(evidence) != "Importada del core"
            or evidence.get("accounting_classification") in {APPLICATION, DEBIT_NOTE, "Depósito"}
            or evidence.get("receivable_origin") or evidence.get("generic_distribution")):
        frappe.throw(_("Seleccione una partida del core con evidencia contable original; no una aplicación, depósito ni partida genérica."))
    if not credit.employer or credit.employer == "NO IDENTIFICADA" or evidence.employer != credit.employer:
        frappe.throw(_("El movimiento contable y el saldo a favor deben tener la misma empresa identificada."))
    debit, original_credit = money(evidence.get("source_debit")), money(evidence.get("source_credit"))
    if debit <= 0 or original_credit:
        frappe.throw(_("El saldo a favor requiere un débito original del core como contrapartida."))
    original = debit
    if evidence.source_currency == "NIO":
        rate = decimal_value(evidence.get("source_fx_rate"))
        if rate <= 0:
            frappe.throw(_("La evidencia en C$ requiere una tasa original válida."))
        original = money(debit / rate)
    if original != money(evidence.amount_usd):
        frappe.throw(_("El importe de la partida no coincide con su débito original convertido a US$. Revise la evidencia."))
    if credit.category == "Saldo a favor del cliente":
        matched = False
        for field, normalize in (("client_number", canonical_identifier), ("loan_number", normalize_credit_number)):
            left, right = clean_text(credit.get(field)), clean_text(evidence.get(field))
            if left and right:
                if normalize(left) != normalize(right):
                    frappe.throw(_("El cliente o crédito del movimiento no coincide con el saldo a favor."))
                matched = True
        if not matched:
            source_name = clean_text(evidence.get("source_client_name")) or clean_text(evidence.get("client_name"))
            if not source_name or name_key(source_name) != name_key(credit.get("client_name")):
                frappe.throw(_("Identifique el mismo cliente en ambas partidas por número, crédito o nombre completo antes de compensar."))


def _load_pair(item_name, counterpart, permission="write"):
    # A recovery must share its origin's cash lock with new collections. Acquire
    # it before the pair's row locks to preserve the global lock order.
    from credinomina_reconciliation.receivable_recovery import lock_recovery_origins
    preflight = []
    for name in sorted({item_name, counterpart}):
        doc = frappe.get_doc(DOCTYPE, name)
        doc.check_permission("read")
        doc.check_permission(permission)
        preflight.append(doc)
    lock_recovery_origins(preflight)
    # Stable parent lock order + current child reads prevent double consumption.
    docs = {}
    for name in sorted({item_name, counterpart}):
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
        doc.check_permission("read")
        doc.check_permission(permission)
        docs[name] = doc
    return docs[item_name], docs[counterpart]


@frappe.whitelist()
def preview_compensation(item_name, counterpart):
    left, right = _load_pair(item_name, counterpart)
    _validate_pair(left, right)
    return {"left": _summary(left), "right": _summary(right), "request_key": uuid4().hex,
            "suggested_usd": min(balance(left)["pending_usd"], balance(right)["pending_usd"])}


def _summary(doc, as_of_date=None):
    return {"name": doc.name, "posting_date": doc.posting_date, "employer": doc.employer,
            "voucher": doc.voucher, "description": doc.description,
            "is_credit": doc.category in CREDIT_CATEGORIES,
            "client_name": doc.get("client_name") or doc.get("source_client_name") or "",
            "client_number": doc.get("client_number") or "",
            "management_pending_usd": doc.get("credit_pending_usd") if doc.category in CREDIT_CATEGORIES else None,
            **balance(doc, as_of_date)}


@frappe.whitelist(methods=["POST"])
def confirm_compensation(item_name, counterpart, amount_usd, compensation_date, reason, request_key):
    if not re.fullmatch(r"[a-f0-9]{32}", request_key or ""):
        frappe.throw(_("Abra de nuevo el selector de partidas para confirmar."))
    left, right = _load_pair(item_name, counterpart, "submit")
    left.check_permission("write")
    right.check_permission("write")
    _validate_pair(left, right)
    amount = money(amount_usd)
    reason = (reason or "").strip()
    if not compensation_date or not reason or amount <= 0:
        frappe.throw(_("Indique fecha, motivo e importe positivo de la compensación."))
    date = getdate(compensation_date)
    if date > getdate(nowdate()) or date < max(getdate(left.posting_date), getdate(right.posting_date)):
        frappe.throw(_("La fecha no puede ser futura ni anterior a ninguno de los movimientos."))
    existing = [[row for row in doc.get("compensations", []) if row.operation_id == request_key] for doc in (left, right)]
    if any(existing):
        if all(len(rows) == 1 for rows in existing) and all(
            money(rows[0].amount_usd) == amount and getdate(rows[0].compensation_date) == date
            and rows[0].reason == reason and rows[0].counterpart == other.name
            for rows, other in zip(existing, (right, left))
        ):
            return {"left": _summary(left), "right": _summary(right)}
        frappe.throw(_("Esta confirmación ya se utilizó con datos diferentes."))
    for doc in (left, right):
        if amount > money(balance(doc)["pending_usd"]):
            frappe.throw(_("El importe supera el saldo pendiente de {0}.").format(doc.name))
        if any(getdate(row.compensation_date) > date for row in doc.get("compensations", [])):
            frappe.throw(_("La fecha no puede preceder a una compensación ya registrada en esta partida."))
    from credinomina_reconciliation.receivable_recovery import validate_compensation_recovery
    validate_compensation_recovery((left, right), amount)
    # No explicit commit: both halves and their Version records share one transaction.
    for index, doc in enumerate(sorted((left, right), key=lambda item: item.name)):
        other = right if doc.name == left.name else left
        if doc.category not in CREDIT_CATEGORIES:
            doc.category = doc.review_action = CATEGORY
        doc.append("compensations", {"operation_id": request_key, "entry_key": f"{request_key}-{index}",
            "counterpart": other.name, "compensation_date": date, "amount_usd": money_float(amount),
            "reason": reason, "confirmed_by": frappe.session.user, "confirmed_on": now_datetime()})
        doc.flags.compensation_write_token = _WRITE_TOKEN
        try:
            if doc.docstatus == 0:
                doc.submit()
            else:
                doc.save()
        finally:
            doc.flags.pop("compensation_write_token", None)
    return {"left": _summary(left), "right": _summary(right)}


@frappe.whitelist()
def get_compensation_balance(item_name, as_of_date):
    if not as_of_date:
        frappe.throw(_("Indique la fecha de corte."))
    doc = frappe.get_doc(DOCTYPE, item_name)
    doc.check_permission("read")
    return _summary(doc, as_of_date)


@frappe.whitelist()
def get_reversible_compensations(item_name):
    doc = frappe.get_doc(DOCTYPE, item_name)
    doc.check_permission("read")
    doc.check_permission("write")
    reversed_ids = {row.reverses_operation_id for row in doc.compensations if row.get("reverses_operation_id")}
    return {"request_key": uuid4().hex, "rows": [
        {field: row.get(field) for field in ("operation_id", "counterpart", "compensation_date", "amount_usd", "reason")}
        for row in doc.compensations if money(row.amount_usd) > 0 and row.operation_id not in reversed_ids
    ]}


@frappe.whitelist(methods=["POST"])
def reverse_compensation(item_name, operation_id, reversal_date, reason, request_key):
    """Append the opposite entry to both ledgers in one transaction."""
    if not re.fullmatch(r"[a-f0-9]{32}", request_key or "") or request_key == operation_id:
        frappe.throw(_("Abra de nuevo el selector de compensaciones para revertir."))
    source = frappe.get_doc(DOCTYPE, item_name)
    source.check_permission("read")
    selected = next((row for row in source.compensations if row.operation_id == operation_id), None)
    if not selected or money(selected.amount_usd) <= 0 or selected.get("reverses_operation_id"):
        frappe.throw(_("Seleccione una compensación original válida."))
    left, right = _load_pair(item_name, selected.counterpart, "submit")
    left.check_permission("write")
    right.check_permission("write")
    _validate_pair(left, right)
    reason = (reason or "").strip()
    if not reason or not reversal_date:
        frappe.throw(_("Indique fecha y motivo de la reversión."))
    date = getdate(reversal_date)
    originals = [[row for row in doc.compensations if row.operation_id == operation_id] for doc in (left, right)]
    if not all(len(rows) == 1 for rows in originals):
        frappe.throw(_("No se encuentran ambas partes de la compensación. Revise el historial."))
    amount = money(originals[0][0].amount_usd)
    if amount <= 0 or any(money(rows[0].amount_usd) != amount or rows[0].counterpart != other.name
                         or rows[0].get("reverses_operation_id")
                         for rows, other in zip(originals, (right, left))):
        frappe.throw(_("Las dos partes de la compensación no coinciden."))
    existing = [[row for row in doc.compensations if row.operation_id == request_key] for doc in (left, right)]
    if any(existing):
        if all(len(rows) == 1 for rows in existing) and all(
            money(rows[0].amount_usd) == -amount and rows[0].reverses_operation_id == operation_id
            and getdate(rows[0].compensation_date) == date and rows[0].reason == reason
            and rows[0].counterpart == other.name for rows, other in zip(existing, (right, left))
        ):
            return {"left": _summary(left), "right": _summary(right)}
        frappe.throw(_("Esta confirmación ya se utilizó con otros datos."))
    if any(row.get("reverses_operation_id") == operation_id for doc in (left, right) for row in doc.compensations):
        frappe.throw(_("La compensación ya fue revertida."))
    latest = max(getdate(row.compensation_date) for doc in (left, right) for row in doc.compensations)
    if date > getdate(nowdate()) or date < latest:
        frappe.throw(_("La reversión no puede ser futura ni anterior a las compensaciones registradas."))
    if any(money(balance(doc)["compensated_usd"]) < amount for doc in (left, right)):
        frappe.throw(_("El saldo compensado no coincide con el historial."))
    for index, doc in enumerate(sorted((left, right), key=lambda item: item.name)):
        other = right if doc.name == left.name else left
        doc.append("compensations", {"operation_id": request_key, "entry_key": f"{request_key}-{index}",
            "reverses_operation_id": operation_id, "counterpart": other.name,
            "compensation_date": date, "amount_usd": -money_float(amount), "reason": reason,
            "confirmed_by": frappe.session.user, "confirmed_on": now_datetime()})
        doc.flags.compensation_write_token = _WRITE_TOKEN
        try:
            doc.save()
        finally:
            doc.flags.pop("compensation_write_token", None)
    return {"left": _summary(left), "right": _summary(right)}
