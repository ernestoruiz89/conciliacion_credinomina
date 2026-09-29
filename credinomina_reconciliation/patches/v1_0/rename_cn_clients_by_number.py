"""Use core client numbers as document names without merging identities."""

from uuid import uuid4

import frappe
from frappe import _
from frappe.model.naming import validate_name

from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def execute():
    doctype = "CN Client"
    if not frappe.db.table_exists(doctype):
        return
    if frappe.get_meta(doctype).autoname != "field:client_number":
        frappe.throw(_("Sincronice CN Client antes de ejecutar este parche."))
    clients = frappe.get_all(doctype, fields=["name", "client_number"],
                             order_by="name asc", limit_page_length=0)
    targets = {}
    seen = {}
    # Preflight the whole set before renaming any record. Missing numbers must
    # be supplied by the operator, never manufactured from an old series name.
    for client in clients:
        target = clean_text(client.client_number)
        if not target:
            frappe.throw(_(
                "El cliente {0} no tiene Nro. Cliente. Complételo y vuelva a ejecutar migrate."
            ).format(client.name))
        key = canonical_identifier(target)
        if key in seen:
            frappe.throw(_(
                "El número de cliente {0} está repetido en {1} y {2}. Corrija los datos antes de migrar."
            ).format(target, seen[key], client.name))
        seen[key] = client.name
        validate_name(doctype, target)
        targets[client.name] = target

    occupied = {canonical_identifier(value) for value in [*targets, *targets.values()]}
    renames = []
    for old, target in targets.items():
        if old == target:
            continue
        temporary = "CN-CLIENT-MIG-" + uuid4().hex
        while canonical_identifier(temporary) in occupied:
            temporary = "CN-CLIENT-MIG-" + uuid4().hex
        occupied.add(canonical_identifier(temporary))
        renames.append((old, temporary, target))

    # Two phases also handle existing names that are another client's number.
    # Frappe updates Link fields and alias child-table parents in both phases.
    for old, temporary, _target in renames:
        frappe.rename_doc(doctype, old, temporary, force=True,
                          show_alert=False, rebuild_search=False)
    for _old, temporary, target in renames:
        frappe.rename_doc(doctype, temporary, target, force=True,
                          show_alert=False, rebuild_search=False)
    for target in targets.values():
        # Also normalize whitespace on documents already named correctly.
        frappe.db.set_value(doctype, target, "client_number", target, update_modified=False)
