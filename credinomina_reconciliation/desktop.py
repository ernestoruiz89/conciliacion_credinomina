"""Desktop entry visibility follows the app's existing operational roles."""
import frappe


APP_ROLES = {"System Manager", "Supervisor Credinomina", "Operador Credinomina"}


def check_app_permission():
    user = frappe.session.user
    if user == "Guest":
        return False
    if user == "Administrator":
        return True
    if frappe.get_cached_value("User", user, "user_type") != "System User":
        return False
    return bool(APP_ROLES.intersection(frappe.get_roles(user))) and frappe.has_permission(
        "CN Reconciliation Period", "read"
    )
