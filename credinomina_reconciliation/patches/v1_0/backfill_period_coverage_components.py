"""Populate the new deposit and complementary coverage fields on existing periods."""
import frappe

from credinomina_reconciliation.period_totals import PeriodTotalsContext


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_reconciliation_period")
    last = ""
    while True:
        names = frappe.get_all("CN Reconciliation Period", filters={"name": [">", last]},
                               pluck="name", order_by="name asc", limit_page_length=200)
        if not names:
            return
        periods = [frappe.get_doc("CN Reconciliation Period", name) for name in names]
        context = PeriodTotalsContext(periods)
        for period in periods:
            values = context.values(period)
            frappe.db.set_value("CN Reconciliation Period", period.name, {
                "deposit_coverage_usd": values["deposit_coverage_usd"],
                "complementary_coverage_usd": values["complementary_coverage_usd"],
            }, update_modified=False)
        last = names[-1]
