import frappe
from frappe import _
from frappe.utils import cint, nowdate

from credinomina_reconciliation.company_transactions import (
    IMPORTED, application_state, deposit_state, monthly_counts, application_month_amounts, deposit_month_amounts,
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    raw_year = str(filters.get("year") or nowdate()[:4])
    if not raw_year.isdigit() or not 1900 <= int(raw_year) <= 9998:
        frappe.throw(_("Indique un año válido entre 1900 y 9998."))
    year = int(raw_year)
    kind = filters.get("transaction_type") or "Aplicaciones"
    if kind not in {"Aplicaciones", "Depósitos"}:
        frappe.throw(_("Seleccione Aplicaciones o Depósitos."))
    drafts = bool(cint(filters.get("include_drafts")))
    doctype = "CN Accounting Import" if kind == "Aplicaciones" else "CN Remittance Allocation"
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(_("No tiene permiso para consultar estas transacciones."), frappe.PermissionError)
    dates = [f"{year}-01-01", f"{year}-12-31"]
    scoped = {"employer": filters.employer} if filters.get("employer") else {}
    records = []
    if kind == "Aplicaciones":
        # Accounting imports are not submittable; their business status defines drafts.
        statuses = sorted(IMPORTED | ({"Borrador"} if drafts else set()))
        parents = frappe.get_list(doctype, filters={**scoped, "status": ["in", statuses], "docstatus": ["!=", 2]},
            fields=["name", "employer", "status", "historical_period", "historical_backfill"], limit_page_length=0)
        by_name = {parent.name: parent for parent in parents}
        names = list(by_name)
        sources = []
        for offset in range(0, len(names), 500):
            # Access child rows only through permission-filtered parent records.
            rows = frappe.get_all("CN Source Row", filters={"parent": ["in", names[offset:offset + 500]],
                "parenttype": doctype, "parentfield": "rows", "event_type": "Aplicacion",
                "event_date": ["between", dates], "docstatus": ["!=", 2]},
                fields=["name", "parent", "event_date", "effective", "match_status", "deposit_match_status",
                        "historical_remitted_usd", "application_adjustment_usd", "event_type", "amount", "currency",
                        "equivalent_amount", "equivalent_currency", "fx_basis", "manual_fx_rate",
                        "historical_period", "historical_detail", "processing_route", "collection_row_id", "application_allocation_detail",
                        "client", "client_number", "national_id", "client_name", "loan_number"], limit_page_length=0)
            sources.extend(rows)
            for row in rows:
                parent = by_name[row.parent]
                records.append({"employer": parent.employer, "date": row.event_date, "state": application_state(row, parent)})
        amounts = application_month_amounts(sources, by_name, load_partial_collections(sources, by_name))
    else:
        rows = frappe.get_list(doctype, filters={**scoped, "docstatus": ["in", [0, 1]] if drafts else 1,
            "deposit_date": ["between", dates]}, fields=["name", "employer", "deposit_date", "docstatus", "result",
                "amount_usd", "allocated_usd", "justified_surplus_usd", "unclassified_usd"], limit_page_length=0)
        records = [{"employer": row.employer, "date": row.deposit_date, "state": deposit_state(row)} for row in rows]
        amounts = deposit_month_amounts(rows)
    data = monthly_counts(records, year, amounts)
    message = _("Verde: todas las transacciones conciliadas. Parciales: naranja si el importe conciliado es menor al 50%; amarillo desde el 50%. "
                "Rojo: todas pendientes. —: sin transacciones. Pase el cursor sobre un mes para ver el desglose. "
                "El porcentaje usa importes US$ del mes, no cantidades de transacciones. Si no se puede atribuir un pago compartido al mes "
                "o falta conversión, el porcentaje no está disponible y se mantiene naranja. "
                "Cada transacción permanece en el mes de su fecha y considera toda su cobertura, aunque los depósitos, aplicaciones o períodos relacionados sean de otros meses. "
                "El color refleja el estado actual, no un corte histórico; no se ejecuta ninguna conciliación.")
    message += " " + (_("Aplicaciones: una transacción por fila de aplicación, según Fecha del movimiento; no por documento ni por período. "
                        "Conciliar con la cobranza no equivale a conciliar el depósito. Importaciones fallidas y registros cancelados se excluyen.")
        if kind == "Aplicaciones" else _("Depósitos: un registro por depósito, según Fecha del depósito y empresa pagadora, aunque cubra otras empresas o períodos. "
            "Un saldo a favor completamente documentado puede estar conciliado aunque su devolución siga pendiente. Se excluyen los cancelados."))
    if drafts:
        message += " " + _("Los borradores se incluyen como pendientes; en aplicaciones se utiliza el estado Borrador de la importación.")
    summary = [{"label": _(label), "value": sum(record["state"] == state for record in records), "datatype": "Int", "indicator": color}
        for state, label, color in (("Conciliado", "Conciliadas", "Green"), ("Parcial", "Parcialmente conciliadas", "Orange"),
                                    ("Pendiente", "Pendientes", "Red"))]
    return columns(), data, message, None, summary


def load_partial_collections(sources, imports):
    """Read only the collection claims needed for partial operative applications."""
    from credinomina_reconciliation.accounting_client_summary import collection_links
    from credinomina_reconciliation.reconciliation import converted_amount
    ids = set()
    for row in sources:
        parent = imports[row["parent"]]
        historical = row.get("historical_period") or parent.get("historical_period") or parent.get("historical_backfill") or row.get("processing_route") == "Historica"
        if not historical and application_state(row, parent) == "Parcial":
            ids.update(link.get("collection_row_id") for link in collection_links(row, converted_amount(row, "USD")))
    ids.discard(None)
    ids.discard("")
    if not ids or not frappe.has_permission("CN Reconciliation Period", "read"):
        return {}
    parents = frappe.get_list("CN Reconciliation Period", pluck="name", limit_page_length=0)
    if not parents:
        return {}
    result = {}
    names = sorted(ids)
    for offset in range(0, len(names), 500):
        result.update({row.name: row for row in frappe.get_all("CN Collection Row",
            filters={"name": ["in", names[offset:offset + 500]], "parent": ["in", parents],
                     "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows"},
            fields=["name", "parent", "applied_usd", "remittance_detail", "rounding_adjustment_usd"], limit_page_length=0)})
    return result


def columns():
    months = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sept", "Oct", "Nov", "Dic"]
    return [{"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 240},
            *[{"fieldname": f"m{index:02}", "label": _(label), "fieldtype": "Int", "width": 70}
              for index, label in enumerate(months, 1)],
            {"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 85}]
