import frappe


def before_install():
    """Create the two small operational roles without requiring ERPNext."""
    for role_name in ("Operador Credinomina", "Supervisor Credinomina"):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(
                ignore_permissions=True
            )
