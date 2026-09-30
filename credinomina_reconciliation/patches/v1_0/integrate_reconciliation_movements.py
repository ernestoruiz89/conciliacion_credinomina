"""Move tolerance audit records without recalculating money or closed periods.

Keep the old SQL table as a recovery archive; remove only DocType metadata.
"""
import frappe
from frappe.utils import getdate

from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.tolerance_items import CATEGORY, AUDIT_FIELDS

OLD = "CN Reconciliation Movement"
NEW = "CN Complementary Item"


def complementary_values(row):
    values = {field: row.get(field) for field in (
        *AUDIT_FIELDS, "name", "status", "owner", "creation", "modified", "modified_by",
        "employer", "period", "deposit_reference", "deposit_date",
    )}
    values.update({
        "doctype": NEW, "category": CATEGORY, "docstatus": 1,
        "movement_key": row.get("movement_key") or row["name"],
        "naming_series": "CN-COMP-.YYYY.-.#####", "currency": "USD",
        "usd_currency": "USD", "nio_currency": "NIO",
        "amount": row["signed_amount_usd"], "amount_usd": row["signed_amount_usd"],
        "reference": row.get("deposit_reference") or row["name"],
        "posting_date": getdate(row.get("deposit_date") or row["creation"]),
        "description": row.get("reason"), "accounting_status": "No requiere registro",
    })
    return values


def _move_references():
    from frappe.model.dynamic_links import get_dynamic_links

    for field in get_dynamic_links():
        if field.parent == OLD:
            continue
        if frappe.db.get_value("DocType", field.parent, "issingle"):
            if frappe.db.get_single_value(field.parent, field.options) == OLD:
                frappe.db.set_single_value(field.parent, field.options, NEW)
        elif frappe.db.table_exists(field.parent):
            frappe.db.set_value(field.parent, {field.options: OLD}, field.options, NEW, update_modified=False)
    for doctype, field, extra in (
        ("Version", "ref_doctype", {}), ("File", "attached_to_doctype", {}),
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


def migrate_records():
    if not frappe.db.table_exists(OLD):
        return
    rows = frappe.db.sql("select * from `tabCN Reconciliation Movement` order by creation, name", as_dict=True)
    for row in rows:
        if row.movement_key and row.movement_key != row.name:
            frappe.throw(f"Clave inesperada para el movimiento {row.name}; revise antes de migrar.")
        migrated = frappe.db.get_value(NEW, {"movement_key": row.name}, ["name", "category"], as_dict=True)
        if migrated:
            if migrated.name != row.name or migrated.category != CATEGORY:
                frappe.throw(f"No se puede migrar {row.name}: destino incompatible.")
            continue  # Preserve subsequent reversion/reactivation on a retry.
        if frappe.db.exists(NEW, row.name):
            frappe.throw(f"No se puede migrar {row.name}: ya existe una partida con ese identificador.")
        frappe.get_doc(complementary_values(row)).db_insert()
        copied = frappe.db.get_value(NEW, row.name,
            ["amount_usd", "signed_amount_usd", "absorbed_cash_usd", "tolerance_usd", "status", "movement_key"], as_dict=True)
        if (not copied or copied.status != row.status or copied.movement_key != row.name
                or money(copied.amount_usd) != money(row.signed_amount_usd)
                or any(money(copied[field]) != money(row[field]) for field in
                       ("signed_amount_usd", "absorbed_cash_usd", "tolerance_usd"))):
            frappe.throw(f"No se verificó la migración del movimiento {row.name}.")


def execute():
    migrate_records()
    _move_references()
    if frappe.db.exists("DocType", OLD):
        # No DROP TABLE: retain original data for recovery.
        frappe.delete_doc("DocType", OLD, force=True, ignore_permissions=True, for_reload=True)
    frappe.clear_cache()
