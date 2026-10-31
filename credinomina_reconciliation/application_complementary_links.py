"""Minimal, permission-scoped navigation to confirmed application adjustments."""
from collections import defaultdict

import frappe

from credinomina_reconciliation.application_deposit_links import _entries
from credinomina_reconciliation.report_records import PAGE_SIZE, records
from credinomina_reconciliation.reclassification_distribution import APPLICATION, FIELD
from credinomina_reconciliation.rounding import money

JOURNALS = ("application_adjustment_history", "credit_history", FIELD)


def load_links(sources, *, include_amounts=False):
    if not sources or not frappe.has_permission("CN Complementary Item", "read"):
        return {}
    parents = {source["name"]: source["parent"] for source in sources}
    names = sorted(parents)
    candidates = set()
    for offset in range(0, len(names), 100):
        batch = names[offset:offset + 100]
        # Restricted journals are queried internally for candidate IDs only.
        # No names or evidence escape until the readable-parent query below.
        candidates.update(row.name for row in frappe.get_all("CN Complementary Item",
            filters={"docstatus": 1}, or_filters=[
                ["related_application", "in", batch], ["application_source_row", "in", batch],
                *[[field, "like", "%" + name + "%"] for field in JOURNALS for name in batch],
            ], fields=["name"], limit_page_length=0))
    readable = {}
    candidate_names = sorted(candidates)
    for offset in range(0, len(candidate_names), PAGE_SIZE):
        for item in records("CN Complementary Item", filters={
                "name": ["in", candidate_names[offset:offset + PAGE_SIZE]], "docstatus": 1},
                fields=["name", "category", "posting_date", "reference", "related_application", "related_import",
                        "application_adjustment_usd", "application_source_row", "status", "signed_amount_usd"]):
            readable[item.name] = item
    result = defaultdict(dict)
    visible_names = sorted(readable)
    for offset in range(0, len(visible_names), PAGE_SIZE):
        # Read only readable origins. Project exact links to applications the
        # caller already read; never return the journals or their other targets.
        journals = frappe.get_all("CN Complementary Item", filters={
            "name": ["in", visible_names[offset:offset + PAGE_SIZE]], "docstatus": 1},
            fields=["name", *JOURNALS], limit_page_length=0)
        for raw in journals:
            item = readable[raw.name]
            targets = defaultdict(lambda: money(0))

            def add(name, amount, parent=None):
                if name in parents and (not parent or parent == parents[name]):
                    targets[name] += money(amount)

            if item.category == "Ajuste de aplicación":
                if money(item.application_adjustment_usd) > 0:
                    add(item.related_application, item.application_adjustment_usd, item.related_import)
                for entry in _entries(raw.get("application_adjustment_history")):
                    if money(entry.get("application_adjustment_usd")) > 0:
                        add(entry.get("related_application"), entry["application_adjustment_usd"], entry.get("related_import"))
            if item.category == "Compensación entre partidas":
                for entry in _entries(raw.get(FIELD)):
                    if entry.get("kind") == APPLICATION and money(entry.get("amount_usd")) > 0:
                        add(entry.get("target"), entry["amount_usd"], entry.get("parent_document"))
            if item.category in {"Saldo a favor del cliente", "Saldo a favor de la empresa"}:
                history = _entries(raw.get("credit_history"))
                reversed_ids = {entry["reverses"] for entry in history if entry.get("reverses")}
                for entry in history:
                    if (not entry.get("reverses") and entry.get("operation_id") not in reversed_ids
                            and money(entry.get("importe_usd")) > 0):
                        add(entry.get("application_id"), entry["importe_usd"], entry.get("import_name"))
            if item.category == "Diferencia por tolerancia" and item.status == "Vigente":
                add(item.application_source_row, -money(item.get("signed_amount_usd")))
            for name, amount in targets.items():
                result[name][item.name] = dict(document=item.name, date=str(item.posting_date or ""),
                                               reference=item.reference or "")
                if include_amounts:
                    result[name][item.name].update(amount_usd=float(amount), category=item.category)
    return {name: sorted(items.values(), key=lambda row: (row["date"], row["document"]))
            for name, items in result.items()}
