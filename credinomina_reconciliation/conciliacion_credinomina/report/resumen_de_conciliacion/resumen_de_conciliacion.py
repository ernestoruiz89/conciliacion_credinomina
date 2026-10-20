import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.reconciliation_report import add_portfolio_status, build_views, load_direct_applications


def execute(filters=None):
    filters = frappe._dict(filters or {})
    conditions = {}
    for fieldname in ("employer", "status", "reconciliation_mode"):
        if filters.get(fieldname):
            conditions[fieldname] = filters[fieldname]
    if filters.get("period"):
        conditions["name"] = filters.period
    if filters.from_month and filters.to_month:
        conditions["payroll_month"] = ["between", [filters.from_month, filters.to_month]]
    elif filters.from_month:
        conditions["payroll_month"] = [">=", filters.from_month]
    elif filters.to_month:
        conditions["payroll_month"] = ["<=", filters.to_month]

    data = list(records(
        "CN Reconciliation Period",
        filters=conditions,
        fields=[
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle",
            "historical_scope", "status", "applied_usd", "no_collection", "cutoff_date",
        ],
        order_by="payroll_month desc, employer asc",
    ))
    if filters.collection_cycle:
        data = [
            row for row in data
            if (
                row.collection_cycle
                or ("Mensual" if row.reconciliation_mode != "Historica" else "")
            ) == filters.collection_cycle
        ]
    if filters.historical_scope:
        data = [
            row for row in data
            if row.reconciliation_mode == "Historica"
            and (row.historical_scope or "Mensual") == filters.historical_scope
        ]
    for row in data:
        row["closure_state"] = "Cerrado" if row.status == "Cerrado" else "Abierto"
        row["usd_currency"] = "USD"
    collection_rows = list(child_records(
        "CN Collection Row", [row.name for row in data if row.reconciliation_mode != "Historica"],
        "CN Reconciliation Period", "collection_rows", fields=[
            "name", "parent", "client", "client_name", "national_id", "loan_number",
            "expected_usd", "expected_nio", "applied_usd", "remitted_usd",
            "rounding_adjustment_usd", "remittance_detail", "first_exception_comment", "comments", "application_comment",
        ],
    ))
    summary, detail = build_views(data, collection_rows, load_direct_applications([row.name for row in data]))
    if filters.get("view") == "Detalle":
        add_portfolio_status(data, detail)
    rows = detail if filters.get("view") == "Detalle" else summary
    if cint(filters.get("only_differences")):
        rows = [row for row in rows if row["has_difference"]]
    # Frappe totals treat unknown amounts as zero. Suppress that misleading total.
    incomplete = any(row[field] is None for row in rows
                     for field in ("application_usd", "deposit_usd", "open_usd"))
    incomplete = incomplete or any(row["collection_usd"] is None and row["collection_control"] != "No aplica"
                                   for row in rows)
    message = _("Totales omitidos: hay importes no determinables. Revise las observaciones.") if incomplete else None
    return get_columns(filters.get("view") == "Detalle"), rows, message, None, None, incomplete


def get_summary_columns():
    return get_columns()


def get_columns(detail=False):
    columns = [
        {"fieldname": "name", "label": _("Período"), "fieldtype": "Link", "options": "CN Reconciliation Period", "width": 175},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 100},
    ]
    if detail:
        columns.extend([
            {"fieldname": "client_number", "label": _("N.º cliente"), "fieldtype": "Data", "width": 100},
            {"fieldname": "client_name", "label": _("Cliente"), "fieldtype": "Data", "width": 220},
            {"fieldname": "loan_number", "label": _("Crédito"), "fieldtype": "Data", "width": 125},
            {"fieldname": "portfolio_credit_status", "label": _("Estado del crédito según cartera"), "fieldtype": "Data", "width": 220},
        ])
    columns.extend({"fieldname": field, "label": _(label), "fieldtype": "Currency",
                    "options": "usd_currency", "width": width} for field, label, width in (
        ("collection_usd", "Cobranza enviada US$", 155),
        ("application_usd", "Aplicación neta US$", 150),
        ("collection_difference_usd", "Cobranza − aplicación US$", 185),
        ("deposit_usd", "Depósito asignado US$", 165),
        ("deposit_difference_usd", "Aplicación − depósito US$", 185),
        ("credit_used_usd", "Saldo a favor utilizado US$", 180),
        ("cash_adjustment_usd", "Ajustes de conciliación US$", 185),
        ("open_usd", "Pendiente por cubrir US$", 175),
    ))
    columns.extend([
        {"fieldname": "collection_control", "label": _("Control cobranza"), "fieldtype": "Data", "width": 145},
        {"fieldname": "cash_control", "label": _("Control cobertura"), "fieldtype": "Data", "width": 145},
    ])
    if not detail:
        columns.extend([
            {"fieldname": "credit_count", "label": _("Clientes/créditos"), "fieldtype": "Int", "width": 130},
            {"fieldname": "difference_count", "label": _("Con diferencias"), "fieldtype": "Int", "width": 125},
        ])
    else:
        columns.extend([
            {"fieldname": "difference_reason", "label": _("Razón de la diferencia"), "fieldtype": "Data", "width": 220},
            {"fieldname": "comments", "label": _("Comentarios"), "fieldtype": "Data", "width": 320},
            {"fieldname": "application_comment", "label": _("Comentario de Aplicación"), "fieldtype": "Data", "width": 320},
        ])
        columns.append({"fieldname": "first_exception_comment", "label": _("Comentario de primera conciliación"),
                        "fieldtype": "Data", "width": 320})
    columns.extend([
        {"fieldname": "closure_state", "label": _("Cierre"), "fieldtype": "Data", "width": 95},
        {"fieldname": "observations", "label": _("Observaciones"), "fieldtype": "Data", "width": 320},
    ])
    return columns
