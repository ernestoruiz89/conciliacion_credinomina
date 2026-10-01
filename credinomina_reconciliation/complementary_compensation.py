"""Dated, paired offsets of accounting evidence; never cash or loan payments."""
import re
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import getdate, get_datetime, nowdate, now_datetime

from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money

CATEGORY = "Compensación entre partidas"
DOCTYPE = "CN Complementary Item"
_WRITE_TOKEN = object()
_ENTRY_FIELDS = ("name", "usd_currency", "operation_id", "counterpart", "compensation_date", "amount_usd", "reason", "confirmed_by", "confirmed_on", "entry_key")
_FROZEN_FIELDS = ("category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "period",
                  "related_application", "related_import", "registered_deposit", "client_number", "loan_number", "description", "reference")


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
    if values:
        doc.review_status = values["status"]


def guard_delete(doc):
    if doc.get("compensations"):
        frappe.throw(_("No se puede eliminar, cancelar o renombrar una partida con compensaciones confirmadas."))


def _eligible(doc):
    if doc.docstatus == 2 or (doc.docstatus == 1 and doc.category != CATEGORY) or doc.category in {"Ajuste de aplicación", "Saldo a favor de la empresa", "Diferencia por tolerancia"}:
        frappe.throw(_("Seleccione borradores o partidas en compensación, sin ajustes de aplicación, saldos a favor ni tolerancias."))
    if doc.related_application or doc.registered_deposit or doc.review_action in {"Ajuste de aplicación", "Partida de depósito", "Reversión identificada"}:
        frappe.throw(_("La partida está destinada a una aplicación o depósito; revise ese vínculo antes de compensarla."))
    if doc.period and frappe.db.get_value("CN Reconciliation Period", doc.period, "status") == "Cerrado":
        frappe.throw(_("La partida pertenece a un período cerrado. Reábralo antes de compensar."))
    if frappe.db.exists("CN Remittance Target", {"complementary_item": doc.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida está seleccionada en un depósito y no puede compensarse también."))


def _direction(doc):
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
    if left.employer and right.employer and left.employer != right.employer:
        frappe.throw(_("Las partidas pertenecen a empresas diferentes."))
    if left.source_account and right.source_account and left.source_account != right.source_account:
        frappe.throw(_("Las partidas corresponden a cuentas contables diferentes."))
    if not _direction(left) or _direction(left) == _direction(right):
        frappe.throw(_("Las partidas deben tener sentidos opuestos: débito y crédito, o importes manuales de signo contrario."))


def _load_pair(item_name, counterpart, permission="write"):
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
            "voucher": doc.voucher, "description": doc.description, **balance(doc, as_of_date)}


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
    # No explicit commit: both halves and their Version records share one transaction.
    for index, doc in enumerate(sorted((left, right), key=lambda item: item.name)):
        other = right if doc.name == left.name else left
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
