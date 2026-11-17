"""Create, link and load a period's accounting/payroll evidence atomically."""
import frappe
from frappe import _

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


@frappe.whitelist(methods=["POST"])
def create(values, source_link=None):
    values = frappe.parse_json(values) if isinstance(values, str) else values
    if not isinstance(values, dict):
        frappe.throw(_("Datos del período inválidos."))
    # Only creation inputs; never accept computed balances, rows or saved names.
    fields = {field: values.get(field) for field in (
        "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "cutoff_date", "collection_file",
    )}
    period = frappe.get_doc({"doctype": "CN Reconciliation Period", **fields})
    return insert_period(period, source_link, period.insert)


def insert_period(period, source_link, insert):
    load = period.reconciliation_mode == "Operativa" and bool(period.collection_file)
    frappe.has_permission("CN Reconciliation Period", "create", throw=True)
    if load:
        frappe.has_permission("CN Reconciliation Period", "write", throw=True)
        frappe.has_permission("CN Accounting Import", "write", throw=True)
        file_filters = {"file_url": period.collection_file}
        if temporary_name := period.get("__temporary_name"):
            # A full-form upload may share a URL with another attachment.
            temporary_file = frappe.db.exists("File", {**file_filters, "attached_to_doctype": period.doctype,
                "attached_to_name": temporary_name, "attached_to_field": "collection_file"})
            if temporary_file:
                file_filters = temporary_file
        frappe.get_doc("File", file_filters).check_permission("read")
    source, row = _source_link(source_link, period.employer) if source_link else (None, None)
    insert()
    if source:
        if row is None:
            source.historical_period = period.name
        else:
            row.historical_period = period.name
        source.save()  # Normal cash, evidence, assignment and closed-period guards.
    summary = None
    # A row-specific Link must not reload/reassign the rest of its import.
    load_movements = source is not None and row is None
    collection_result = None
    if load:
        if load_movements:
            # Load the requested basis before refreshing the core. The core
            # loader then reconciles once, against the newly loaded payroll.
            collection_result = api._import_collection(period.name, reconcile=False)
        else:
            collection_result = api.import_collection(period.name)
    movement_result = None
    if load_movements:
        # Use the implementation without its standalone transaction retry:
        # a rollback/retry here would discard the period we just inserted.
        # Default=False preserves periods already assigned to individual rows.
        movement_result = accounting._import_source_file(source.name, apply_default_period=False)
    if load:
        period.reload()
        reconciliation = movement_result if movement_result is not None else collection_result.get("source_reconciliation")
        if reconciliation is not None:
            summary = api._first_reconciliation_result(period, reconciliation)
    elif load_movements:
        period.reload()
    return {"doc": period.as_dict(), "collection_loaded": load, "reconciliation": summary,
            "source_import": movement_result.get("import_name", source.name) if movement_result is not None
                else source.name if source else None, "movements_loaded": load_movements,
            "movement_rows": movement_result.get("rows", 0) if movement_result else 0}


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
