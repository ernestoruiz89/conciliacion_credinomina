"""Create a period and, when supplied, load its payroll in one transaction."""
import frappe
from frappe import _

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api


@frappe.whitelist(methods=["POST"])
def create(values, source_link=None):
    values = frappe.parse_json(values) if isinstance(values, str) else values
    if not isinstance(values, dict):
        frappe.throw(_("Datos del período inválidos."))
    # Only creation inputs; never accept computed balances, rows or saved names.
    fields = {field: values.get(field) for field in (
        "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "cutoff_date", "collection_file",
    )}
    load = fields["reconciliation_mode"] == "Operativa" and bool(fields["collection_file"])
    frappe.has_permission("CN Reconciliation Period", "create", throw=True)
    if load:
        frappe.has_permission("CN Reconciliation Period", "write", throw=True)
        frappe.has_permission("CN Accounting Import", "write", throw=True)
        frappe.get_doc("File", {"file_url": fields["collection_file"]}).check_permission("read")
    source, row = _source_link(source_link, fields["employer"]) if load and source_link else (None, None)
    period = frappe.get_doc({"doctype": "CN Reconciliation Period", **fields}).insert()
    if source:
        if row is None:
            source.historical_period = period.name
        else:
            row.historical_period = period.name
        source.save()  # Normal cash, evidence, assignment and closed-period guards.
    summary = None
    if load:
        # import_collection already runs the period-scoped first reconciliation
        # with preserve_deposits=True. Do not run the same engine a second time.
        result = api.import_collection(period.name)
        period.reload()
        if result.get("source_reconciliation") is not None:
            summary = api._first_reconciliation_result(period, result["source_reconciliation"])
    return {"doc": period.as_dict(), "collection_loaded": load, "reconciliation": summary,
            "source_import": source.name if source else None}


def _source_link(link, employer):
    link = frappe.parse_json(link) if isinstance(link, str) else link
    if not isinstance(link, dict) or not link.get("name") or not link.get("modified"):
        frappe.throw(_("El vínculo de la importación no es válido. Actualice el formulario."))
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    lock_cash_pool([employer])
    source = frappe.get_doc("CN Accounting Import", link["name"], for_update=True)
    source.check_permission("write")
    if source.employer != employer or source.docstatus == 2:
        frappe.throw(_("La importación no está disponible para esta empresa."))
    if str(source.modified) != str(link["modified"]):
        frappe.throw(_("La importación cambió. Actualice el formulario antes de crear el período."))
    row = None
    if link.get("row_name"):
        row = next((row for row in source.rows if row.name == link["row_name"] and row.event_type == "Aplicacion"), None)
        if row is None:
            frappe.throw(_("La aplicación no pertenece a esta importación."))
    if (row.historical_period if row is not None else "") or source.historical_period:
        frappe.throw(_("La importación o la fila ya tiene un período asignado. Conserve sus vínculos y revise esa asignación antes de cambiarla."))
    return source, row
