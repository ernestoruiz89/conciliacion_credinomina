"""Move company credits to complementary items without recalculating closed periods.

The old SQL table is intentionally retained as a recovery archive. Only its
DocType metadata is removed, after copying all records and their links.
"""
import frappe
from frappe.utils import getdate

from credinomina_reconciliation.company_credit import CATEGORY

OLD = "CN Deposit Surplus"
NEW = "CN Complementary Item"


def complementary_values(row):
    deposit_date = frappe.db.get_value(
        "CN Remittance Allocation", row.registered_deposit, "deposit_date"
    ) if row.registered_deposit else None
    return {
        "doctype": NEW, "name": row.name,
        "docstatus": row.docstatus, "owner": row.owner,
        "creation": row.creation, "modified": row.modified,
        "modified_by": row.modified_by, "idx": row.idx,
        "naming_series": "CN-COMP-.YYYY.-.#####",
        "usd_currency": "USD", "nio_currency": "NIO", "currency": "USD",
        "category": CATEGORY, "amount": row.amount_usd, "amount_usd": row.amount_usd,
        "period": row.period, "employer": row.employer,
        "registered_deposit": row.registered_deposit,
        "reference": row.deposit_reference, "deposit_voucher": row.deposit_voucher,
        "posting_date": getdate(deposit_date or row.creation),
        "reason_type": row.reason_type, "description": row.explanation,
        "support_file": row.support_file, "result": row.result,
        # A bank receipt is NOT evidence of an accounting entry for this credit.
        "accounting_status": "Pendiente de registro",
        "legacy_surplus_id": row.name, "legacy_surplus_snapshot": frappe.as_json(row),
    }


def _move_references():
    from frappe.model.dynamic_links import get_dynamic_links

    # Comments, files, versions, shares and other dynamic references retain IDs.
    for field in get_dynamic_links():
        if field.parent == OLD:
            continue
        if frappe.db.get_value("DocType", field.parent, "issingle"):
            if frappe.db.get_single_value(field.parent, field.options) == OLD:
                frappe.db.set_single_value(field.parent, field.options, NEW)
        elif frappe.db.table_exists(field.parent):
            frappe.db.set_value(field.parent, {field.options: OLD}, field.options, NEW, update_modified=False)

    # Preserve custom links, permissions and navigation pointing to the records.
    for doctype, field, extra in (
        ("Version", "ref_doctype", {}),
        ("File", "attached_to_doctype", {}),
        ("DocShare", "share_doctype", {}),
        ("DocField", "options", {"fieldtype": "Link"}),
        ("Custom Field", "options", {"fieldtype": "Link"}),
        ("Property Setter", "value", {"property": "options"}),
        ("User Permission", "allow", {}),
        ("Workspace Link", "link_to", {"link_type": "DocType"}),
        ("Workspace Shortcut", "link_to", {"type": "DocType"}),
        ("Report", "ref_doctype", {}),
    ):
        if frappe.db.table_exists(doctype):
            frappe.db.set_value(doctype, {field: OLD, **extra}, field, NEW, update_modified=False)


def execute():
    if frappe.db.table_exists(OLD):
        rows = frappe.db.sql("select * from `tabCN Deposit Surplus` order by creation, name", as_dict=True)
        for row in rows:
            migrated = frappe.db.get_value(NEW, {"legacy_surplus_id": row.name}, "name")
            if migrated:
                # Do not overwrite later edits when the patch is retried.
                if migrated != row.name:
                    frappe.throw(f"No se puede migrar {row.name}: identificador de destino inesperado {migrated}.")
                continue
            if frappe.db.exists(NEW, row.name):
                frappe.throw(f"No se puede migrar {row.name}: ya existe una partida con ese identificador.")
            document = frappe.get_doc(complementary_values(row))
            # Transformation only: preserve submitted/cancelled records, timestamps
            # and balances. Normal insert/submit would recalculate closed periods.
            document.db_insert()
            copied = frappe.db.get_value(NEW, row.name, ["amount_usd", "docstatus", "legacy_surplus_id"], as_dict=True)
            if (copied.amount_usd != row.amount_usd or copied.docstatus != row.docstatus
                    or copied.legacy_surplus_id != row.name):
                frappe.throw(f"No se verificó la migración del excedente {row.name}.")
    _move_references()
    if frappe.db.exists("DocType", OLD):
        # for_reload prevents developer-mode controller deletion. Frappe removes
        # DocType metadata, not its SQL data table. No DROP TABLE is performed.
        frappe.delete_doc("DocType", OLD, force=True, ignore_permissions=True, for_reload=True)
    frappe.clear_cache()
