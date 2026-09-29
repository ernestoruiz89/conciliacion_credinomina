"""Copy stored portfolio JSON values into queryable child-row columns."""

import json

import frappe
from frappe import _

from credinomina_reconciliation.parsers import portfolio_source_values_from_raw_data


def execute():
    doctype = "CN Credit Portfolio Row"
    if not frappe.db.table_exists(doctype):
        return
    if not frappe.db.has_column(doctype, "fecha_reporte"):
        frappe.throw(_("Sincronice CN Credit Portfolio Row antes de copiar sus columnas."))

    rows = frappe.get_all(
        doctype,
        fields=["name", "raw_data"],
        limit_page_length=1000000,
    )
    for row in rows:
        if not row.raw_data:
            continue
        try:
            raw_data = json.loads(row.raw_data)
        except (TypeError, ValueError) as exc:
            frappe.throw(_("La fila {0} tiene Datos originales inválidos: {1}").format(
                row.name, exc
            ))
        values = portfolio_source_values_from_raw_data(raw_data)
        if values:
            frappe.db.set_value(doctype, row.name, values, update_modified=False)
