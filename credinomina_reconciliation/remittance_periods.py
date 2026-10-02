"""Selected reconciliation periods, separate from a deposit's receipt date."""
from collections import defaultdict


def selected_periods(document):
    rows = document.get("detail_periods") if hasattr(document, "get") else getattr(document, "detail_periods", [])
    return list(dict.fromkeys(row.get("period") for row in (rows or []) if row.get("period")))


def attach_periods(deposits):
    """Batch-load children only for already scoped/authorized deposit parents."""
    import frappe
    unloaded = [deposit for deposit in deposits if (
        deposit.get("detail_periods") if hasattr(deposit, "get") else getattr(deposit, "detail_periods", None)
    ) is None]
    names = [deposit.name for deposit in unloaded]
    by_parent = defaultdict(list)
    for offset in range(0, len(names), 500):
        for row in frappe.get_all("CN Remittance Period", filters={
            "parent": ["in", names[offset:offset + 500]],
            "parenttype": "CN Remittance Allocation", "parentfield": "detail_periods",
        }, fields=["parent", "period"], order_by="idx asc", limit_page_length=0):
            by_parent[row.parent].append(row)
    for deposit in unloaded:
        deposit.detail_periods = by_parent[deposit.name]
    return deposits


def deposit_names_for_periods(periods):
    """Internal lookup; callers must still apply parent permission filters."""
    import frappe
    if not periods:
        return []
    return list(set(frappe.get_all("CN Remittance Period", filters={
        "period": ["in", list(periods)], "parenttype": "CN Remittance Allocation",
        "parentfield": "detail_periods",
    }, pluck="parent", limit_page_length=0)))
