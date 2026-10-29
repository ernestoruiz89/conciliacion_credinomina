"""List candidates, then close each period in a separate HTTP transaction."""
import frappe
from frappe import _

PERIOD = "CN Reconciliation Period"


@frappe.whitelist()
def list_periods(filters=None, period_names=None):
    frappe.has_permission(PERIOD, "write", throw=True)
    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
    period_names = frappe.parse_json(period_names) if isinstance(period_names, str) else period_names
    if filters is not None and not isinstance(filters, list):
        frappe.throw(_("Los filtros de la lista no son válidos. Actualice la lista."))
    if period_names is not None and (
        not isinstance(period_names, list)
        or any(not isinstance(name, str) or not name for name in period_names)
    ):
        frappe.throw(_("La selección de períodos no es válida."))
    # Selected records take precedence over current filters; otherwise preserve
    # all list filters and include every page. get_list enforces read access.
    scope = [["name", "in", list(dict.fromkeys(period_names))]] if period_names else list(filters or [])
    scope.extend([["status", "=", "Conciliado"], ["docstatus", "<", 2]])
    return frappe.get_list(
        PERIOD, filters=scope, fields=["name", "employer", "payroll_month"],
        order_by="payroll_month asc, employer asc, name asc", limit_page_length=0,
    )


@frappe.whitelist(methods=["POST"])
def close_reconciled_period(period_name: str):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period

    period = frappe.get_doc(PERIOD, period_name)
    period.check_permission("write")
    if period.docstatus == 2 or period.status != "Conciliado":
        frappe.throw(_("El período ya no está conciliado o fue cancelado. Estado actual: {0}.").format(period.status))
    # Never catch a closure error here: a failed HTTP request must roll back
    # reconciliation updates as well as the closure. Other periods have their
    # own requests and remain committed even if this one fails.
    return close_period(period.name)
