"""Flag inherited cases for review; never resolve them or remove their block."""
import frappe
from credinomina_reconciliation.exception_policy import LEGACY_TYPES
from credinomina_reconciliation.report_records import _pages


def execute():
    for row in _pages(frappe.get_all, "CN Reconciliation Exception", filters={},
                      fields=["name", "control_area", "blocks_closure", "exception_type",
                              "complementary_item", "classification_reason"], order_by="name asc"):
        if row.classification_reason or row.control_area not in {None, "", "Financiera"}:
            continue
        changes = {"control_area": "Registro contable", "blocks_closure": 0} if row.complementary_item else {
            "control_area": "Legado por revisar" if row.exception_type in LEGACY_TYPES else "Financiera",
            "blocks_closure": 1,
        }
        frappe.db.set_value("CN Reconciliation Exception", row.name, changes, update_modified=False)
