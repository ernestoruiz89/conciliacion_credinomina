"""Number legacy hash exceptions without reconciling or changing financial data."""
import re

import frappe
from frappe import _

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_exception.cn_reconciliation_exception import (
    exception_name_prefix,
    new_exception_name,
)

DOCTYPE = "CN Reconciliation Exception"
HASH_NAME = re.compile(r"[a-z0-9]{10}", re.ASCII)
# These are live presentation caches, not Links. Saved cut files/Version payloads
# are historical evidence and must retain their original identifiers and bytes.
PRESENTATION_FIELDS = (
    ("CN Collection Row", "remittance_detail"),
    ("CN Remittance Allocation", "allocation_detail"),
)


def replace_exception_references(value, renamed):
    """Replace only exception_id values; keep amounts, other IDs and formatting."""
    return re.sub(
        r'("exception_id"\s*:\s*")([a-z0-9]{10})(")',
        lambda match: match[1] + renamed.get(match[2], match[2]) + match[3],
        value,
    )


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    if not frappe.get_meta(DOCTYPE).has_field("legacy_name"):
        frappe.throw(_("Sincronice Excepciones antes de renombrar los registros."))
    pending = [row for row in frappe.get_all(
        DOCTYPE, fields=["name", "period", "creation"],
        order_by="creation asc, name asc", limit_page_length=0,
    ) if HASH_NAME.fullmatch(row.name)]
    # Detect missing/invalid period dates before touching any document/series.
    for row in pending:
        exception_name_prefix(row.period, row.creation)
    renamed = {}
    for row in pending:
        new = new_exception_name(row.period, row.creation)
        frappe.db.set_value(DOCTYPE, row.name, "legacy_name", row.name, update_modified=False)
        frappe.rename_doc(DOCTYPE, row.name, new, force=True, merge=False,
                          show_alert=False, rebuild_search=False)
        renamed[row.name] = new
    if not renamed:
        return
    for doctype, field in PRESENTATION_FIELDS:
        if not frappe.db.table_exists(doctype) or not frappe.get_meta(doctype).has_field(field):
            continue
        for row in frappe.get_all(doctype, filters={field: ["like", '%"exception_id"%']},
                                  fields=["name", field], limit_page_length=0):
            previous = row.get(field) or ""
            updated = replace_exception_references(previous, renamed)
            if updated != previous:
                frappe.db.set_value(doctype, row.name, field, updated, update_modified=False)
    frappe.clear_cache()
