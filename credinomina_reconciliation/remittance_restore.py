"""Recover historical deposit evidence without reviving invalid destinations."""
import frappe


def prepare_restore(document):
    if not (document.flags.get("from_restore") and document.get("accounting_source_key")
            and document.docstatus in (0, 2)):
        return []

    names = {row.complementary_item for row in document.get("targets") or []
             if row.complementary_item}
    if not names:
        return []
    statuses = {row.name: row.docstatus for row in frappe.get_all(
        "CN Complementary Item", filters={"name": ["in", list(names)]},
        fields=["name", "docstatus"],
    )}
    removed, retained = [], []
    for row in document.targets:
        if row.complementary_item and statuses.get(row.complementary_item) in (None, 2):
            removed.append({"fila": row.idx, "partida": row.complementary_item,
                            "importe_usd": row.amount_usd})
        else:
            retained.append(row)
    if not removed:
        return []

    document.docstatus = 0
    document.set("targets", retained)
    document.result = "Pendiente"
    document.allocated_usd = 0
    document.allocation_detail = "[]"
    for idx, row in enumerate(document.get("targets") or [], 1):
        row.idx = idx
        row.result = "Pendiente"
    if document.get("detail_rows"):
        document.detail_status = "Cargado; pendiente de conciliación"
    for row in document.get("detail_rows") or []:
        row.match_status = "Pendiente"
        row.match_reason = "Depósito restaurado; requiere conciliación."
        row.matched_targets = "[]"
        row.matched_targets_summary = ""
    return removed
