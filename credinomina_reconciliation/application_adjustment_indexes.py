"""Selective current reads for application adjustments, including JSON links.

Generated flags index journal presence without maintaining a second financial
journal. They follow ordinary saves and db_set atomically, including reversal.
"""
import frappe

DOCTYPE = "CN Complementary Item"
HISTORY_FLAG = "cn_has_application_history"
RECLASS_FLAG = "cn_has_reclassification"
DIRECT_INDEX = "cn_adjustment_application"
HISTORY_INDEX = "cn_adjustment_history"
RECLASS_INDEX = "cn_adjustment_reclassification"

FLAGS = ((HISTORY_FLAG, "application_adjustment_history"),
         (RECLASS_FLAG, "reclassification_allocations"))
INDEXES = (
    (["related_application", "docstatus", "category"], DIRECT_INDEX),
    ([HISTORY_FLAG, "docstatus", "employer"], HISTORY_INDEX),
    ([RECLASS_FLAG, "docstatus"], RECLASS_INDEX),
)


def ensure_indexes():
    # has_column uses a cached description which can predate this migration.
    columns = {row.Field for row in frappe.db.sql("SHOW COLUMNS FROM `tabCN Complementary Item`", as_dict=True)}
    missing = [(flag, field) for flag, field in FLAGS if flag not in columns]
    if missing:
        frappe.db.sql_ddl("ALTER TABLE `tabCN Complementary Item` " + ", ".join(
            f"ADD COLUMN `{flag}` TINYINT GENERATED ALWAYS AS "
            f"(COALESCE(`{field}`, '') NOT IN ('', '[]')) VIRTUAL"
            for flag, field in missing))
    for fields, index in INDEXES:
        frappe.db.add_index(DOCTYPE, fields, index_name=index)
