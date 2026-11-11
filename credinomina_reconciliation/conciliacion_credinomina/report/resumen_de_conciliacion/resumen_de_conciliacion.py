import frappe
from frappe import _
from frappe.utils import cint, get_first_day

from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.parsers import collection_comment
from credinomina_reconciliation.reconciliation_report import add_portfolio_status, build_views, load_direct_applications
from credinomina_reconciliation.reconciliation_pending_detail import load as load_pending_detail
from credinomina_reconciliation.report_employers import selected_employers
from credinomina_reconciliation.reconciliation_client_detail import consolidate as consolidate_clients
from credinomina_reconciliation.reconciliation_report_cash import apply_period_cash


def execute(filters=None):
    filters = frappe._dict(filters or {})
    by_client = filters.get('view') == 'Detalle por Cliente'
    is_detail = by_client or filters.get('view') == 'Detalle'
    conditions = {}
    for fieldname in ("employer", "status", "reconciliation_mode"):
        if filters.get(fieldname):
            conditions[fieldname] = filters[fieldname]
    companies = selected_employers(filters)
    if companies:
        conditions['employer'] = ['in', companies]
    if filters.get("period"):
        conditions["name"] = filters.period
    for field in ("from_month", "to_month"):
        if filters.get(field):
            filters[field] = get_first_day(filters[field])
    if filters.from_month and filters.to_month:
        if filters.from_month > filters.to_month:
            frappe.throw(_("El mes inicial debe ser anterior o igual al mes final."))
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
            "negative_complementary_usd", "cash_breakdown_known", "cash_breakdown_note", "deposit_coverage_usd",
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
        row["collection_cycle"] = row.collection_cycle or ("Mensual" if row.reconciliation_mode != "Historica" else "")
        row["closure_state"] = "Cerrado" if row.status == "Cerrado" else "Abierto"
        row["usd_currency"] = "USD"
    collection_rows = list(child_records(
        "CN Collection Row", [row.name for row in data if row.reconciliation_mode != "Historica"],
        "CN Reconciliation Period", "collection_rows", fields=[
            "name", "parent", "row_key", "client", "client_name", "national_id", "loan_number",
            "expected_usd", "expected_nio", "applied_usd", "remitted_usd",
            "rounding_adjustment_usd", "remittance_detail", "first_exception_comment", "comments", "application_comment",
            "cxc_difference_comment",
        ],
    ))
    claims = {}
    summary, detail = build_views(data, collection_rows, load_direct_applications([row.name for row in data]), cash_claims=claims)
    if is_detail:
        for row in detail:
            row["comments"] = "\n".join(
                f"{_(label)}: {text}" for field, label in (
                    ("comments", "Comentarios"), ("application_comment", "Aplicación"),
                    ("cxc_difference_comment", "Diferencias CXC"),
                ) if (text := collection_comment(row.get(field)))
            )
        pending = load_pending_detail(data, filters)
        # Unassociated receipts must not be assigned an invented reporting period.
        associated = [row for row in pending if row.get('name')]
        add_portfolio_status(data, [*detail, *associated])
        detail.extend(pending)
    client_by_detail = {}
    rows = consolidate_clients(detail, client_by_detail=client_by_detail) if by_client else detail if is_detail else summary
    apply_period_cash(data, summary, detail, claims, client_by_detail=client_by_detail)
    if cint(filters.get("only_differences")):
        rows = [row for row in rows if row["has_difference"]]
    for row in rows:
        row.pop('national_id', None)
    # Frappe totals treat unknown amounts as zero. Suppress that misleading total.
    incomplete = any(row[field] is None for row in rows
                     for field in ("application_usd", "deposit_usd", "open_usd", "cash_adjustment_usd"))
    incomplete = incomplete or any(row["collection_usd"] is None and row["collection_control"] != "No aplica"
                                   for row in rows)
    if by_client:
        incomplete = incomplete or any(row[field] is None for row in rows
            for field in ('deposit_unallocated_usd', 'complementary_unallocated_usd'))
    message = _("Totales omitidos: hay importes no determinables. Revise las observaciones.") if incomplete else None
    independent = any(row.get('_pending_receipt') for row in rows)
    if independent and not incomplete:
        message = _("Totales omitidos: el detalle incluye remanentes independientes pendientes de aplicar; no se suman como cobertura de las aplicaciones.")
    return get_columns(is_detail, client_detail=by_client, show_month=cint(filters.get('show_month')),
                       show_period=cint(filters.get('show_period'))), rows, message, None, None, incomplete or independent


def get_summary_columns():
    return get_columns()


def get_columns(detail=False, *, client_detail=False, show_month=False, show_period=False):
    detail = detail or client_detail
    columns = [
        {"fieldname": "name", "label": _("Período"), "fieldtype": "Link", "options": "CN Reconciliation Period", "width": 175},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 150},
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
        ("deposit_usd", "Depósito asignado / pendiente US$" if detail else "Depósito asignado US$", 190 if detail else 165),
        ("deposit_difference_usd", "Aplicación − depósito US$", 185),
        ("credit_used_usd", "Saldo a favor utilizado US$", 180),
        ("cash_adjustment_usd", "Complementarias / ajustes US$" if detail else "Ajustes de conciliación US$", 185),
        ("conversion_usd", "Diferencia de empresa US$", 190),
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
            {"fieldname": "comments", "label": _("Comentarios"), "fieldtype": "Data", "width": 380},
        ])
        columns.append({"fieldname": "first_exception_comment", "label": _("Comentario de primera conciliación"),
                        "fieldtype": "Data", "width": 320})
    columns.extend([
        {"fieldname": "closure_state", "label": _("Cierre"), "fieldtype": "Data", "width": 95},
        {"fieldname": "observations", "label": _("Observaciones"), "fieldtype": "Data", "width": 320},
    ])
    if client_detail:
        columns = [column for column in columns
                   if (column['fieldname'] != 'name' or show_period)
                   and (column['fieldname'] != 'payroll_month' or show_month)]
        for column in columns:
            if column['fieldname'] in ('name', 'payroll_month', 'employer'):
                column['fieldtype'] = 'Data'
                column.pop('options', None)
                column['width'] = 220
            elif column['fieldname'] == 'deposit_usd':
                column['label'] = _('Depósito asignado US$')
            elif column['fieldname'] == 'cash_adjustment_usd':
                column['label'] = _('Ajustes de conciliación US$')
            elif column['fieldname'] == 'loan_number':
                column['width'] = 220
        index = next(i for i, column in enumerate(columns) if column['fieldname'] == 'deposit_usd') + 1
        columns[index:index] = [dict(fieldname=field, label=_(label), fieldtype='Currency', options='usd_currency', width=190)
            for field, label in (('deposit_unallocated_usd', 'Depósito no asignado US$'),
                                 ('complementary_unallocated_usd', 'Partida no asignada US$'))]
    return columns
