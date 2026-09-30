"""Automatic tolerance adjustments share the complementary-item register."""
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.rounding import money, money_float

CATEGORY = "Diferencia por tolerancia"
DOCTYPE = "CN Complementary Item"
_writing = ContextVar("cn_tolerance_item_write", default=False)
AUDIT_FIELDS = (
    "movement_key", "deposit_source_row", "application_source_row", "claim_id",
    "signed_amount_usd", "absorbed_cash_usd", "tolerance_usd", "core_applied_usd",
    "deposit_usd", "claim_usd", "reason", "reversed_on", "reversal_reason",
)


@contextmanager
def tolerance_item_write():
    token = _writing.set(True)
    try:
        yield
    finally:
        _writing.reset(token)


def is_tolerance_item(doc):
    return getattr(doc, "category", None) == CATEGORY or bool(getattr(doc, "movement_key", None))


def guard_tolerance_item(doc, previous=None):
    automatic = is_tolerance_item(doc) or (previous and is_tolerance_item(previous))
    if automatic and not _writing.get():
        frappe.throw(_("Las diferencias por tolerancia las administra la conciliación automática; no se pueden crear, editar, cancelar ni eliminar manualmente."))
    if automatic and doc.category != CATEGORY:
        frappe.throw(_("No se puede cambiar la categoría de un ajuste automático."))
    return automatic


def validate_tolerance_item(doc):
    if not doc.movement_key or not doc.period or not doc.employer or not doc.claim_id:
        frappe.throw(_("El ajuste automático requiere clave, empresa, período y destino."))
    delta = money(doc.signed_amount_usd)
    if not delta or money(doc.tolerance_usd) <= 0 or abs(delta) > money(doc.tolerance_usd):
        frappe.throw(_("La diferencia no está dentro de la tolerancia autorizada."))
    if doc.voucher:
        frappe.throw(_("La diferencia por tolerancia es interna y no requiere asiento contable."))
    doc.currency, doc.usd_currency = "USD", "USD"
    doc.amount = doc.amount_usd = money_float(delta)
    doc.accounting_status = "No requiere registro"
    doc.description = doc.reason


def item_values(movement, deposit):
    """Keep deterministic identifiers so embedded allocation links remain valid."""
    return {
        "doctype": DOCTYPE, "category": CATEGORY,
        "movement_key": movement["name"], "status": "Vigente",
        "employer": movement["employer"], "period": movement["period"],
        "reference": deposit.reference or movement["name"],
        "posting_date": getdate(deposit.event_date), "deposit_date": deposit.event_date,
        "deposit_reference": deposit.reference,
        "deposit_source_row": movement["deposit_id"],
        "application_source_row": movement["application_id"], "claim_id": movement["claim_id"],
        "signed_amount_usd": movement["signed_amount_usd"],
        "absorbed_cash_usd": movement["consumed_residual_usd"],
        "tolerance_usd": movement["tolerance_usd"],
        "core_applied_usd": movement["core_applied_usd"], "deposit_usd": movement["deposit_usd"],
        "claim_usd": movement["claim_usd"],
        "reason": _("Diferencia menor dentro de la tolerancia autorizada; control interno, sin asiento contable ni cambio en el core."),
    }
