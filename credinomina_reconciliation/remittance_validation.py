"""Fresh, bounded lookups for one deposit validation; never cache cash across calls."""
from collections import Counter

import frappe

from credinomina_reconciliation.parsers import clean_text


def _records(doctype, names, fields):
    names = sorted(set(names) - {None, ""})
    result = {}
    for offset in range(0, len(names), 500):
        result.update({row.name: row for row in frappe.get_all(
            doctype, filters={"name": ["in", names[offset:offset + 500]]},
            fields=["name", *fields], limit_page_length=0,
        )})
    return result


class TargetValidationContext:
    def __init__(self, targets, detail_periods=()):
        self.applications = _records("CN Source Row",
            (clean_text(target.historical_application) for target in targets),
            ["event_type", "historical_period", "currency", "effective"])
        self.complements = _records("CN Complementary Item",
            (target.complementary_item for target in targets),
            ["docstatus", "period", "amount_usd", "category", "origin_deposit", "employer", "generic_distribution"])
        periods = {row.period for row in detail_periods}
        periods.update(target.period for target in targets)
        periods.update(row.historical_period for row in self.applications.values())
        periods.update(row.period for row in self.complements.values())
        self.periods = _records("CN Reconciliation Period", periods,
            ["employer", "status", "applied_usd"])
        self._collections = {}
        self._generic_items = {}

    def collection(self, name):
        # Loading each period once retains the document's normal not-found
        # behavior and avoids repeatedly hydrating all its collection rows.
        if name not in self._collections:
            period = frappe.get_doc("CN Reconciliation Period", name)
            self._collections[name] = (period, Counter(row.row_key for row in period.collection_rows))
        return self._collections[name]

    def generic_item(self, name):
        if name not in self._generic_items:
            item = frappe.get_doc("CN Complementary Item", name)
            item.check_permission("read")
            self._generic_items[name] = item
        return self._generic_items[name]

    def period_value(self, name, field):
        return self.periods.get(name, {}).get(field)


def allocation_has_closed_period(entries):
    periods = {entry.get("periodo") for entry in entries}
    items = {entry.get("partida") for entry in entries if not entry.get("periodo")}
    periods.update(row.period for row in _records("CN Complementary Item", items, ["period"]).values())
    return any(row.status == "Cerrado" for row in
               _records("CN Reconciliation Period", periods, ["status"]).values())
