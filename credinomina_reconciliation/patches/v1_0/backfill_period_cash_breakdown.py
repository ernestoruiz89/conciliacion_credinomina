"""Refresh presentation-only cash components without replaying reconciliations."""
import frappe

from credinomina_reconciliation.period_totals import PeriodTotalsContext
from credinomina_reconciliation.rounding import money


FIELDS = ("deposit_coverage_usd", "complementary_coverage_usd", "negative_complementary_usd",
          "cash_breakdown_known", "cash_breakdown_note")


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
            if any(money(values[field]) != money(period.get(field))
                   for field in ('applied_total_usd', 'remitted_total_usd', 'pending_usd')):
                # Do not certify a new split against stale parent balances,
                # or repair those balances by replaying a closed reconciliation.
                values = dict(values, deposit_coverage_usd=None, complementary_coverage_usd=None,
                    negative_complementary_usd=None, cash_breakdown_known=0,
                    cash_breakdown_note='Los totales guardados del período no coinciden con su detalle; '
                        'el desglose queda sin determinar y la conciliación se conserva.')
            # Currency columns are NOT NULL. The explicit flag and explanation
            # preserve unknown attribution without treating stored zero as fact.
            stored = {field: (0 if values[field] is None and field.endswith('_usd') else values[field])
                      for field in FIELDS}
            frappe.db.set_value("CN Reconciliation Period", period.name,
                stored, update_modified=False)
        last = names[-1]
