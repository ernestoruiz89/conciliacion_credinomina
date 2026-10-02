"""Index selective closure checks without changing financial records."""
import frappe


INDEXES = (
    ("CN Remittance Period", ["period", "parent"], "cn_closure_selected_period"),
    ("CN Remittance Target", ["period", "parent"], "cn_closure_target_period"),
    ("CN Remittance Target", ["historical_application", "parent"], "cn_closure_target_application"),
    ("CN Remittance Target", ["complementary_item", "parent"], "cn_closure_target_complement"),
    ("CN Source Row", ["historical_period", "event_type", "effective"], "cn_closure_historical_application"),
    ("CN Source Row", ["collection_period", "event_type", "effective"], "cn_closure_operative_application"),
)


def execute():
    # Frappe's add_index is idempotent. Run after schema sync (and after a new
    # installation, whose patch log is populated without executing old patches).
    for doctype, fields, name in INDEXES:
        frappe.db.add_index(doctype, fields, index_name=name)
