"""Rename deposits without recomputing money, allocations or closed periods."""

from html import escape

import frappe
from frappe import _

from credinomina_reconciliation.deposit_naming import (
    DOCTYPE, deposit_prefix, new_deposit_name, uses_deposit_series,
)


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    if not frappe.get_meta(DOCTYPE).has_field("reconciliation_identity"):
        frappe.throw(_("Sincronice Distribución de Depósito antes de renombrar los registros."))
    rows = frappe.get_all(DOCTYPE, fields=["name", "deposit_date", "reconciliation_identity"],
                          order_by="deposit_date asc, creation asc, name asc", limit_page_length=0)
    pending = [row for row in rows if not uses_deposit_series(row.name)]
    # Fail before touching any document if an existing deposit lacks a date.
    for row in pending:
        if not row.deposit_date:
            frappe.throw(_("El depósito {0} no tiene fecha. Complétela y vuelva a migrar.").format(row.name))
        deposit_prefix(row.deposit_date)
    for row in pending:
        old = row.name
        new = new_deposit_name(row.deposit_date)
        # Tolerance hashes used the old deposit id. Preserve that identity so
        # renaming does not create/reverse adjustments or invalidate closures.
        if not row.reconciliation_identity:
            frappe.db.set_value(DOCTYPE, old, "reconciliation_identity", old, update_modified=False)
        frappe.rename_doc(DOCTYPE, old, new, force=True, merge=False,
                          show_alert=False, rebuild_search=False)
        # These legacy/technical references are Data fields, not Frappe Links.
        for doctype, field in (
            ("CN Reconciliation Period", "deduction_recognition_deposit"),
            ("CN Complementary Item", "deposit_source_row"),
        ):
            frappe.db.set_value(doctype, {field: old}, field, new, update_modified=False)
        frappe.get_doc(DOCTYPE, new).add_comment(
            "Info", _("Identificador anterior del depósito: {0}").format(escape(old)),
        )
    frappe.clear_cache()
