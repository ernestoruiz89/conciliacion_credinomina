import frappe


def ensure_core_removal_role():
    from credinomina_reconciliation.accounting_evidence import CORE_REMOVAL_ROLE
    if not frappe.db.exists("Role", CORE_REMOVAL_ROLE):
        frappe.get_doc({"doctype": "Role", "role_name": CORE_REMOVAL_ROLE,
                        "desk_access": 1}).insert(ignore_permissions=True)


def after_install():
    from credinomina_reconciliation.complementary_subcategories import seed_subcategories
    from credinomina_reconciliation.patches.v1_0.index_period_closure_links import execute
    execute()
    seed_subcategories()


def before_install():
    """Create operational roles without requiring ERPNext or assigning users."""
    ensure_core_removal_role()
    for role_name in ("Operador Credinomina", "Supervisor Credinomina"):
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(
                ignore_permissions=True
            )
