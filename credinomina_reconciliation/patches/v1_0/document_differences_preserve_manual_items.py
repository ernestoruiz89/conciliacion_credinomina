"""Install documentary fields and inventory legacy items without replaying money."""
import frappe


def execute():
    for doctype in ("cn_difference_reason", "cn_source_row", "cn_collection_row", "cn_remittance_detail",
                    "cn_remittance_allocation", "cn_complementary_item", "cn_employer"):
        frappe.reload_doc("conciliacion_credinomina", "doctype", doctype)
    frappe.reload_doc("conciliacion_credinomina", "report", "revision_de_partidas_manuales")
    from credinomina_reconciliation.difference_reasons import seed_reasons
    seed_reasons()
    mark_existing_items()
    frappe.clear_cache()


def mark_existing_items():
    """Idempotent data-only phase, independently testable without schema commits."""
    last = ""
    while True:
        rows = frappe.get_all("CN Complementary Item", filters={
            "accounting_source_key": ["is", "not set"], "name": [">", last],
        }, fields=["name", "legacy_manual_review"], order_by="name asc", limit_page_length=500)
        if not rows:
            break
        for row in rows:
            if not row.legacy_manual_review:
                # No save/submit/cancel/reconcile hooks, modified timestamps or links.
                frappe.db.set_value("CN Complementary Item", row.name, "legacy_manual_review", 1,
                                    update_modified=False)
        last = rows[-1].name
