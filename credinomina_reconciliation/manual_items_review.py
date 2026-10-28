"""Permission-scoped inventory of historical manual complementary items."""
import frappe
from frappe import _
from frappe.utils import cint, getdate

from credinomina_reconciliation.report_records import records

ITEM = "CN Complementary Item"
FIELDS = ("name", "employer", "posting_date", "category", "movement_key", "docstatus", "status",
          "reconciliation_status", "amount_usd", "applied_usd", "pending_usd", "client_number",
          "loan_number", "reference", "voucher", "description", "difference_reason", "difference_notes")


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission(ITEM, "read"):
        frappe.throw(_("No tiene permiso para consultar partidas."), frappe.PermissionError)
    if filters.from_date and filters.to_date and getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("La fecha Desde no puede ser posterior a Hasta."))
    scope = {"accounting_source_key": ["is", "not set"]}
    if filters.employer:
        scope["employer"] = filters.employer
    if filters.item:
        scope["name"] = filters.item
    if filters.from_date and filters.to_date:
        scope["posting_date"] = ["between", [filters.from_date, filters.to_date]]
    elif filters.from_date:
        scope["posting_date"] = [">=", filters.from_date]
    elif filters.to_date:
        scope["posting_date"] = ["<=", filters.to_date]

    rows = []
    for item in records(ITEM, filters=scope, fields=list(FIELDS)):
        automatic = item.get("category") == "Diferencia por tolerancia" or bool(item.get("movement_key"))
        if automatic and not cint(filters.get("include_tolerance")):
            continue
        row = {field: item.get(field) for field in FIELDS if field not in {"name", "movement_key", "status"}}
        row.update(item=item.name, origin=_("Tolerancia histórica") if automatic else _("Manual"),
                   tolerance_status=item.get("status") if automatic else "")
        if item.get("reconciliation_status") == "Saldo no disponible":
            row["applied_usd"] = row["pending_usd"] = None
        rows.append(row)

    definitions = [
        ("item", "Partida", "Link", ITEM), ("employer", "Empresa", "Link", "CN Employer"),
        ("posting_date", "Fecha", "Date", None), ("origin", "Origen", "Data", None),
        ("category", "Concepto", "Data", None), ("docstatus", "Docstatus", "Int", None),
        ("tolerance_status", "Estado de tolerancia histórica", "Data", None),
        ("reconciliation_status", "Conciliación guardada", "Data", None),
        ("amount_usd", "Importe USD", "Currency", "USD"),
        ("applied_usd", "Aplicado USD guardado", "Currency", "USD"),
        ("pending_usd", "Pendiente USD guardado", "Currency", "USD"),
        ("client_number", "Nro. cliente", "Data", None), ("loan_number", "Crédito", "Data", None),
        ("reference", "Referencia", "Data", None), ("voucher", "Comprobante", "Data", None),
        ("description", "Descripción", "Data", None),
        ("difference_reason", "Razón de la diferencia", "Link", "CN Difference Reason"),
        ("difference_notes", "Documentación", "Data", None),
    ]
    columns = [dict(fieldname=field, label=_(label), fieldtype=kind, options=options, width=180)
               for field, label, kind, options in definitions]
    message = _("Una fila por partida manual o tolerancia histórica. Los saldos son los guardados; el reporte no consulta ni relaciona vínculos.")
    return columns, rows, message
