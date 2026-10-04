import frappe


def after_install():
    from credinomina_reconciliation.complementary_subcategories import seed_subcategories
    from credinomina_reconciliation.patches.v1_0.index_period_closure_links import execute
    execute()
    seed_subcategories()


def before_install():
    """Create the two small operational roles without requiring ERPNext."""
    for role_name in ("Operador Credinomina", "Supervisor Credinomina"):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(
                ignore_permissions=True
            )
