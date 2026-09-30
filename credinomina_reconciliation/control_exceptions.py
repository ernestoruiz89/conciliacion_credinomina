"""Register a linked exception from an outstanding dashboard application."""
import frappe
from frappe import _

from credinomina_reconciliation.exception_selection import resolve_related_case
from credinomina_reconciliation.rounding import money, money_float

DOCTYPE = "CN Reconciliation Exception"
FIELDS = ["name", "period", "status", "related_case_type", "related_case_id", "source_import", "source_row"]


def exception_for_application(exceptions, row):
    # Prefer open cases; otherwise retain access to the most recent closed case.
    for item in sorted(exceptions, key=lambda e: e.status not in ("Abierta", "En revision")):
        if item.period != row.historical_period:
            continue
        if item.related_case_id:
            matches = item.related_case_type == "Aplicación" and item.related_case_id == row.name
        else:
            # Previously registered exceptions may only have the source location.
            matches = (item.related_case_type in (None, "", "Aplicación")
                       and item.source_import == row.parent and bool(row.source_row)
                       and item.source_row == row.source_row)
        if matches:
            return item


def annotate_application_exceptions(rows):
    if not rows or not frappe.has_permission(DOCTYPE, "read"):
        return
    exceptions = frappe.get_list(DOCTYPE,
        filters={"period": ["in", list({r.historical_period for r in rows})]},
        fields=FIELDS, order_by="modified desc", limit_page_length=0)
    by_id = {(row.historical_period, row.name): row for row in rows}
    by_source = {(row.historical_period, row.parent, row.source_row): row for row in rows if row.source_row}
    for item in sorted(exceptions, key=lambda e: e.status not in ("Abierta", "En revision")):
        if item.related_case_id:
            row = by_id.get((item.period, item.related_case_id)) if item.related_case_type == "Aplicación" else None
        else:
            row = by_source.get((item.period, item.source_import, item.source_row)) if item.related_case_type in (None, "", "Aplicación") else None
        if row is not None and not row.get("exception_name"):
            row.exception_name = item.name
            row.exception_status = item.status


@frappe.whitelist(methods=["POST"])
def create_application_exception(period_name, application_id, expected_pending_usd, description,
                                 cause_category="Por determinar"):
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw(_("No tiene permiso para registrar excepciones."), frappe.PermissionError)
    # Serialize double clicks/retries against the same application and closure.
    frappe.db.sql("select name from `tabCN Reconciliation Period` where name=%s for update", (period_name,))
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    if period.status == "Cerrado":
        frappe.throw(_("El período está cerrado. Reábralo antes de registrar una excepción."))
    if period.reconciliation_mode != "Historica":
        frappe.throw(_("Seleccione una aplicación del detalle histórico."))
    values = resolve_related_case(period.employer, "Aplicación", application_id, period.name)
    row = frappe.db.get_value("CN Source Row", application_id,
        ["name", "parent", "source_row", "historical_period", "historical_balance_usd",
         "effective", "event_type", "match_status"], as_dict=True, for_update=True)
    if (not row or row.historical_period != period.name or not row.effective
            or row.event_type != "Aplicacion" or row.match_status != "Conciliado"):
        frappe.throw(_("La aplicación cambió. Actualice el control antes de continuar."))
    existing = exception_for_application(frappe.get_all(DOCTYPE,
        filters={"period": period.name}, fields=FIELDS, order_by="modified desc", limit_page_length=0), row)
    if existing:
        if not frappe.has_permission(DOCTYPE, "read", doc=existing.name):
            frappe.throw(_("Esta aplicación ya tiene una excepción registrada. Solicite acceso para consultarla."), frappe.PermissionError)
        return {"name": existing.name, "created": False}
    pending = money(row.historical_balance_usd)
    if pending <= 0:
        frappe.throw(_("La aplicación ya no tiene saldo pendiente. Actualice el control."))
    if pending != money(expected_pending_usd):
        frappe.throw(_("El saldo pendiente cambió. Actualice el control y vuelva a registrar la excepción."))
    if not (description or "").strip():
        frappe.throw(_("Describa el motivo de la excepción."))
    document = frappe.get_doc({
        "doctype": DOCTYPE, **values, "status": "Abierta",
        "exception_type": "Aplicación con saldo pendiente", "amount_usd": money_float(pending),
        "description": description.strip(), "cause_category": cause_category,
    }).insert()
    return {"name": document.name, "created": True}
