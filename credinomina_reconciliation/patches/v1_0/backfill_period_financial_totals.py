"""Populate display totals from existing evidence, including closed periods."""
import frappe

from credinomina_reconciliation.period_totals import PeriodTotalsContext


def execute():
    last = ""
    while True:
        names = frappe.get_all("CN Reconciliation Period", filters={"name": [">", last]},
            pluck="name", order_by="name asc", limit_page_length=200)
        if not names:
            return
        periods = [frappe.get_doc("CN Reconciliation Period", name) for name in names]
        context = PeriodTotalsContext(periods)
        for period in periods:
            frappe.db.set_value("CN Reconciliation Period", period.name,
                context.values(period), update_modified=False)
        last = names[-1]
