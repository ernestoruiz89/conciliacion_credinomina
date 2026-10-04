"""Initialize follow-up only: no inferred refund, actor, due date or cash reallocation."""
import frappe
from credinomina_reconciliation.rounding import money, money_float


def execute():
    for item in frappe.get_all("CN Complementary Item", filters={
        "category": "Saldo a favor de la empresa", "docstatus": ["!=", 2],
    }, fields=["name", "amount_usd", "credit_management_status", "credit_history", "credit_resolved_usd"], limit_page_length=0):
        if item.credit_management_status or item.credit_history not in (None, "", "[]") or money(item.credit_resolved_usd):
            continue  # Never overwrite an existing follow-up, including a resolved zero.
        frappe.db.set_value("CN Complementary Item", item.name, {
            "credit_management_status": "Pendiente", "credit_pending_usd": money_float(item.amount_usd),
            "credit_resolved_usd": 0, "credit_history": "[]", "credit_treatment": "Pendiente de decisión",
        }, update_modified=False)
