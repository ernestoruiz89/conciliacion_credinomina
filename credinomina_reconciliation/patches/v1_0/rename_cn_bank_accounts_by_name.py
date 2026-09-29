"""Make account_name the bank-account document name and preserve links."""

from uuid import uuid4

import frappe
from frappe import _
from frappe.model.naming import validate_name

from credinomina_reconciliation.employer_naming import employer_label_key


def execute():
    doctype = "CN Bank Account"
    if not frappe.db.table_exists(doctype):
        return
    if frappe.get_meta(doctype).autoname != "field:account_name":
        frappe.throw(_("Sincronice CN Bank Account antes de ejecutar este parche."))

    accounts = frappe.get_all(
        doctype,
        fields=["name", "account_name"],
        order_by="name asc",
        limit_page_length=100000,
    )
    if not accounts:
        return

    targets = {}
    seen_targets = {}
    occupied_names = {employer_label_key(row.name) for row in accounts}
    for account in accounts:
        target = str(account.account_name or "").strip()
        if not target:
            frappe.throw(_("La cuenta {0} no tiene Nombre de la cuenta.").format(account.name))
        key = employer_label_key(target)
        if key in seen_targets:
            frappe.throw(_(
                "No se pueden renombrar las cuentas: el nombre {0} está repetido en {1} y {2}."
            ).format(target, seen_targets[key], account.name))
        seen_targets[key] = account.name
        try:
            validate_name(doctype, target)
        except Exception as exc:
            frappe.throw(_("El nombre de cuenta {0} no es válido: {1}").format(target, str(exc)))
        targets[account.name] = target
        occupied_names.add(key)

    renames = []
    for old, target in targets.items():
        if old == target:
            continue
        temporary = f"CN-CTA-MIG-{uuid4().hex}"
        while employer_label_key(temporary) in occupied_names:
            temporary = f"CN-CTA-MIG-{uuid4().hex}"
        occupied_names.add(employer_label_key(temporary))
        renames.append((old, temporary, target))

    # Release all generated names before assigning account names. Link fields,
    # including CN Remittance Allocation.bank_account, are updated by Frappe.
    for old, temporary, _target in renames:
        frappe.rename_doc(
            doctype, old, temporary, force=True,
            show_alert=False, rebuild_search=False,
        )
    for _old, temporary, target in renames:
        frappe.rename_doc(
            doctype, temporary, target, force=True,
            show_alert=False, rebuild_search=False,
        )

    for target in targets.values():
        migrated = frappe.db.get_value(doctype, target, "account_name")
        if migrated != target:
            frappe.throw(_("No se pudo conservar el Nombre de la cuenta {0}.").format(target))
