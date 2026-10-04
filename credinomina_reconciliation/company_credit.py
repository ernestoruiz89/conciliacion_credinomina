"""Company credits live in complementary items, but never settle loan claims."""
import json
import frappe
from frappe import _
from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.remittance_periods import selected_periods

CATEGORY = "Saldo a favor de la empresa"


def ensure_related_periods_open(doc):
    periods = {doc.period} if doc.period else set()
    previous = doc.get_doc_before_save() if callable(getattr(doc, "get_doc_before_save", None)) else None
    if previous and previous.period:
        periods.add(previous.period)
    deposits = {doc.registered_deposit}
    if previous and previous.get("registered_deposit"):
        deposits.add(previous.registered_deposit)
    for name in deposits - {None, ""}:
        deposit = frappe.get_doc("CN Remittance Allocation", name)
        periods.update(selected_periods(deposit))
        for target in deposit.targets or []:
            if target.period:
                periods.add(target.period)
            if target.historical_application:
                period = frappe.db.get_value("CN Source Row", target.historical_application, "historical_period")
                if period:
                    periods.add(period)
            if target.complementary_item:
                period = frappe.db.get_value("CN Complementary Item", target.complementary_item, "period")
                if period:
                    periods.add(period)
        try:
            entries = json.loads(deposit.allocation_detail or "[]")
        except (TypeError, ValueError):
            entries = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if entry.get("periodo"):
                periods.add(entry["periodo"])
            if entry.get("partida"):
                period = frappe.db.get_value("CN Complementary Item", entry["partida"], "period")
                if period:
                    periods.add(period)
    closed = [name for name in periods if frappe.db.get_value("CN Reconciliation Period", name, "status") == "Cerrado"]
    if closed:
        frappe.throw(_("El saldo a favor afecta un período cerrado ({0}). Reábralo antes de modificarlo.").format(", ".join(sorted(closed))))


def validate_company_credit(doc):
    from credinomina_reconciliation.client_credit import MANAGED_FIELDS, _assert_financial_identity, lock_credit_deposit, other_reservations
    previous = doc.get_doc_before_save() if callable(getattr(doc, "get_doc_before_save", None)) else None
    if previous and previous.docstatus == 1:
        _assert_financial_identity(doc, previous)
        for field in MANAGED_FIELDS:
            doc.set(field, previous.get(field))
        return  # Follow-up does not mutate the original cash or closed periods.
    if money(doc.amount_usd) <= 0:
        frappe.throw(_("El saldo a favor de la empresa debe ser positivo."))
    if doc.client_number or doc.loan_number or doc.installment_number or doc.get("credit_client") or doc.get("credit_detail_row"):
        frappe.throw(_("El saldo a favor pertenece a la empresa, no a un cliente o crédito."))
    if not doc.registered_deposit:
        if doc.docstatus == 1:
            frappe.throw(_("Vincule el saldo a favor al depósito confirmado antes de confirmar la partida."))
        migrated = (doc.get("legacy_surplus_id") and doc.name and doc.period
                    and frappe.db.get_value("CN Complementary Item", doc.name, "legacy_surplus_id") == doc.legacy_surplus_id)
        if not migrated:
            frappe.throw(_("Seleccione el depósito al que corresponde el saldo a favor."))
    if not doc.reason_type or not (doc.description or "").strip():
        frappe.throw(_("Indique el motivo y tratamiento del saldo a favor."))
    if not doc.get("credit_assigned_to") or not doc.get("credit_commitment_date"):
        frappe.throw(_("Indique responsable y fecha compromiso del saldo a favor de la empresa."))
    if doc.get("credit_treatment") not in {"Pendiente de decisión", "Devolución", "Aplicación futura"}:
        frappe.throw(_("Seleccione el tratamiento del saldo a favor."))
    if doc.period:
        employer = frappe.db.get_value("CN Reconciliation Period", doc.period, "employer")
        if doc.employer and doc.employer != employer:
            frappe.throw(_("El período no pertenece a la empresa indicada."))
        doc.employer = employer
    if doc.registered_deposit:
        deposit = lock_credit_deposit(doc.registered_deposit)
        deposit.check_permission("read")
        if deposit.docstatus != 1 or not deposit.deposit_date:
            frappe.throw(_("Seleccione un depósito registrado y confirmado."))
        if doc.employer and doc.employer != deposit.employer:
            frappe.throw(_("El depósito y la partida pertenecen a empresas diferentes."))
        doc.employer = deposit.employer
        doc.reference = deposit.deposit_reference
        doc.deposit_voucher = deposit.deposit_voucher
        other = other_reservations(deposit.name, doc.name)
        capacity = money(deposit.amount_usd) - money(deposit.allocated_usd) - sum_money(row.amount_usd for row in other)
        if money(doc.amount_usd) > capacity:
            frappe.throw(_("El saldo a favor supera el efectivo del depósito sin asignar ni documentar."))
    doc.credit_resolved_usd = 0
    doc.credit_pending_usd = money_float(doc.amount_usd)
    doc.credit_management_status = "Pendiente"
    doc.credit_history = "[]"
    if getattr(doc, "_action", None) != "update_after_submit":
        ensure_related_periods_open(doc)
