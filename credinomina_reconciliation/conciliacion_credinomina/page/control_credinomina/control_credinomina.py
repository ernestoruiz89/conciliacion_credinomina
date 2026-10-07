from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from credinomina_reconciliation.aging import collection_shortfall_usd, unassigned_deduction_usd, deduction_pending_type
from credinomina_reconciliation.control_exceptions import annotate_application_exceptions
from credinomina_reconciliation.control_deposits import get_cash_deposits
from credinomina_reconciliation.complementary_balances import cached_balance_reads
from credinomina_reconciliation.control_summary import collection_summaries, historical_difference_counts, readable_imports
from credinomina_reconciliation.tolerance_items import CATEGORY as TOLERANCE_CATEGORY
from credinomina_reconciliation.reconciliation import net_application_amount
from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.historical import OPERATIVE_START
from credinomina_reconciliation.rounding import CASH_EPSILON, money_float
from credinomina_reconciliation.remittance_periods import attach_periods, selected_periods, deposit_names_for_periods


def _row_limit(dashboard_limit: int, full_export: bool) -> int:
    # Frappe interprets zero as unlimited; the screen keeps its bounded payload.
    return 0 if full_export else dashboard_limit


def _unassigned_application_is_historical(row, source_import):
    """Mirror the routing priority used when accounting rows are reconciled."""
    if row.processing_route == "Historica":
        return True
    if row.event_date and getdate(row.event_date) < OPERATIVE_START:
        return True
    if row.processing_route == "Operativa":
        return False
    return bool(source_import.historical_backfill or source_import.historical_period)


@frappe.whitelist(methods=["GET"])
@cached_balance_reads
def export_control_excel(year=None, employer=None):
    """Download a complete, permission-scoped snapshot of the control matrix."""
    required_reads = (
        "CN Reconciliation Period", "CN Accounting Import", "CN Remittance Allocation",
        "CN Complementary Item", "CN Reconciliation Exception",
    )
    if any(not frappe.has_permission(doctype, "read") for doctype in required_reads):
        frappe.throw(_("No tiene permiso para exportar el control completo."))
    from credinomina_reconciliation.control_export import build_control_workbook

    data = _build_control_data(year, employer, full_export=True)
    from credinomina_reconciliation.core_item_position import load_core_items
    data['core_complementary_items'] = load_core_items(cint(data['year']) or None, employer)
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    data['adjustment_receivables'] = load_receivables(employer=employer, year=cint(data['year']) or None)
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute as company_statement
    statement_filters = {"view_mode": "Resumen"}
    if employer:
        statement_filters["employer"] = employer
    statement_year = cint(data["year"])
    if statement_year:
        statement_filters.update(from_date=f"{statement_year}-01-01", to_date=f"{statement_year}-12-31")
    columns, rows, message = company_statement(statement_filters)
    data["company_statement"] = {"columns": columns, "rows": rows, "message": message}
    period_names = [period["name"] for period in data["periods"]]
    exceptions = []
    exception_fields = [
        "name", "period", "employer", "exception_type", "client_number", "creation",
        "loan_number", "amount_usd", "description", "resolution", "status",
        "cause_category", "assigned_to", "next_action", "commitment_date",
        "evidence_file", "external_reference",
    ]
    for offset in range(0, len(period_names), 500):
        exceptions.extend(frappe.get_list(
            "CN Reconciliation Exception",
            filters={"period": ["in", period_names[offset:offset + 500]]},
            fields=exception_fields,
            order_by="period asc, creation asc",
            limit_page_length=0,
        ))
    unlinked_exception_filters = [["period", "is", "not set"]]
    exception_year = cint(data["year"])  # Dashboard returns the label "Todos" for all years.
    if exception_year:
        unlinked_exception_filters.extend([
            ["creation", ">=", f"{exception_year}-01-01"],
            ["creation", "<", f"{exception_year + 1}-01-01"],
        ])
    if employer:
        unlinked_exception_filters.append(["employer", "=", employer])
    exceptions.extend(frappe.get_list(
        "CN Reconciliation Exception",
        filters=unlinked_exception_filters,
        fields=exception_fields, order_by="creation asc", limit_page_length=0,
    ))
    actions = []
    exception_names = [item.name for item in exceptions]
    for offset in range(0, len(exception_names), 500):
        actions.extend(frappe.get_all(
            "CN Exception Action",
            filters={"parent": ["in", exception_names[offset:offset + 500]]},
            fields=[
                "parent", "action_at", "action_by", "action_type", "details",
                "evidence_file", "external_reference",
            ],
            order_by="parent asc, idx asc",
            limit_page_length=0,
        ))
    employer_label = employer or _("Todas las empresas")
    if employer and data["periods"]:
        employer_label = data["periods"][0]["employer_name"]
    content = build_control_workbook(
        data, exceptions=exceptions, actions=actions, employer_label=employer_label,
        generated_at=now_datetime(),
        date_format=frappe.db.get_single_value("System Settings", "date_format") or "yyyy-mm-dd",
    )
    ascii_name = unicodedata.normalize("NFKD", employer or "todas").encode(
        "ascii", "ignore"
    ).decode("ascii")
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-")[:60]
    frappe.local.response.filename = f"control_credinomina_{data['year'] or 'todos'}_{suffix or 'todas'}.xlsx"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    frappe.local.response.content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@frappe.whitelist()
def get_control_data(year=None, employer=None):
    """Permission-scoped, real data for the monthly control matrix."""
    data = _build_control_data(year, employer, summary_only=True)
    # A calendar-year sample must never masquerade as the complete work queue.
    data["work_scope"] = "Todos" if data["year"] == "Todos" else "calendar"
    return data


@frappe.whitelist()
def get_work_overview(employer=None):
    """Lazy all-year work queue, independent from calendar/KPI/export filters."""
    return _build_control_data("Todos", employer, summary_only=True, detail_section="work_overview")


@frappe.whitelist()
@cached_balance_reads
def get_control_kpis(year=None, employer=None):
    """Separate from calendar loading; does not execute reconciliation."""
    if not frappe.has_permission("CN Reconciliation Period", "read"):
        frappe.throw(_("No tiene permiso para consultar la conciliacion."))
    year = None if str(year).strip().casefold() in ("todos", "todo", "all") else cint(year or now_datetime().year)
    if year is not None and not 2000 <= year <= 2100:
        frappe.throw(_("Indique un año valido."))
    from credinomina_reconciliation.control_kpis import get_figures
    return get_figures(year, employer, now_datetime().date())


@frappe.whitelist()
def get_period_detail(period_name: str):
    """Load one visible period; never load the rest of the dashboard for a modal."""
    frappe.get_doc("CN Reconciliation Period", period_name).check_permission("read")
    data = _build_control_data("Todos", detail_period=period_name)
    if not data["periods"]:
        frappe.throw(_("El período no está disponible."), frappe.PermissionError)
    period = data["periods"][0]
    return {"name": period_name, "detail_loaded": True, **{
        field: period[field] for field in (
            "rows", "historical_rows", "exceptions", "surpluses", "rounding_movements",
        )
    }}


@frappe.whitelist()
def get_deposit_detail(deposit_name: str):
    frappe.get_doc("CN Remittance Allocation", deposit_name).check_permission("read")
    deposits = get_cash_deposits(None, deposit_name=deposit_name)
    if not deposits:
        frappe.throw(_("El depósito no está disponible o ya no está confirmado."))
    return {**deposits[0], "detail_loaded": True}


@frappe.whitelist()
def get_control_rows(section: str, year=None, employer=None, start=0, work_kind="", responsible="", due=""):
    if section not in {"open_deposits", "unassigned_historical_applications", "work_items"}:
        frappe.throw(_("Sección de control inválida."))
    start = max(cint(start), 0)
    if section == "unassigned_historical_applications":
        if not frappe.has_permission("CN Reconciliation Period", "read"):
            frappe.throw(_("No tiene permiso para consultar la conciliacion."))
        year = None if str(year).strip().casefold() in ("todos", "todo", "all") else cint(year or now_datetime().year)
        if year is not None and not 2000 <= year <= 2100:
            frappe.throw(_("Indique un año valido."))
        from credinomina_reconciliation.control_application_pages import historical_page
        return historical_page(year, employer, start)
    rows = _build_control_data(year, employer, summary_only=True, detail_section=section)[section]
    if section == "work_items" and (work_kind or responsible or due):
        from credinomina_reconciliation.follow_up_queue import filter_work
        rows = filter_work(rows, work_kind, responsible, due, as_of=now_datetime().date())
    result = {"rows": rows[start:start + 100], "count": len(rows)}
    if section == "work_items":
        result["overdue_count"] = sum(item.get("priority") == 0 for item in rows)
        result['action_count'] = sum(item.get('action_count', 1) for item in rows)
    return result


def _year_filter(field, year):
    return {field: ["between", [f"{year}-01-01", f"{year}-12-31"]]} if year is not None else {}


def _available_years(employer=None):
    """Year choices include visible periods, deposits and imported applications."""
    years = set()
    company = {"employer": employer} if employer else {}
    for doctype, field, extra in (
        ("CN Reconciliation Period", "payroll_month", {}),
        ("CN Remittance Allocation", "deposit_date", {"docstatus": 1}),
    ):
        if frappe.has_permission(doctype, "read"):
            for row in frappe.get_list(doctype, filters={**company, **extra},
                                       fields=[field], group_by=field, limit_page_length=0):
                if row.get(field):
                    years.add(getdate(row[field]).year)
    if frappe.has_permission("CN Accounting Import", "read"):
        imports = frappe.get_list("CN Accounting Import", filters={**company,
            "status": ["in", ["Importado", "Importado con excepciones"]]},
            pluck="name", limit_page_length=0)
        for offset in range(0, len(imports), 500):
            for row in frappe.get_all("CN Source Row",
                filters={"parent": ["in", imports[offset:offset + 500]], "parenttype": "CN Accounting Import",
                         "event_type": "Aplicacion", "effective": 1},
                fields=["event_date"], group_by="event_date", limit_page_length=0):
                if row.event_date:
                    years.add(getdate(row.event_date).year)
    return sorted(years, reverse=True)


@cached_balance_reads
def _build_control_data(year=None, employer=None, *, full_export=False, summary_only=False, detail_period=None, detail_section=None):
    """Build dashboard data; exports can request the complete matching population."""
    if not frappe.has_permission("CN Reconciliation Period", "read"):
        frappe.throw(_("No tiene permiso para consultar la conciliacion."))
    year = None if str(year).strip().casefold() in ("todos", "todo", "all") else cint(year or now_datetime().year)
    if year is not None and not 2000 <= year <= 2100:
        frappe.throw(_("Indique un año valido."))
    # All years must not silently omit older records because of screen limits.
    full_export = full_export or year is None or summary_only or bool(detail_period)
    filters = _year_filter("payroll_month", year)
    if employer:
        filters["employer"] = employer
    if detail_period:
        filters["name"] = detail_period
    periods = frappe.get_list(
        "CN Reconciliation Period",
        filters=filters,
        fields=[
            "remark",
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "historical_scope", "historical_application_date", "historical_start_date", "historical_end_date", "cutoff_date", "remittance_due_date", "status", "deduction_basis", "deduction_recognition_reference", "employer_response_file", "control_cut_on", "control_cut_note",
            *([] if summary_only else ["control_cut_summary"]),
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "rounding_adjustment_usd",
            "remitted_usd", "fx_variance_usd", "exception_count",
        ],
        order_by="employer asc, payroll_month asc",
        limit_page_length=_row_limit(3000, full_export),
    )
    employer_names = {
        item.name: item.employer_name
        for item in frappe.get_all(
            "CN Employer",
            filters={"name": ["in", list({period.employer for period in periods})]},
            fields=["name", "employer_name"],
        )
    } if periods else {}
    period_names = [period.name for period in periods]
    rows_by_period = defaultdict(list)
    historical_rows_by_period = defaultdict(list)
    exceptions_by_period = defaultdict(list)
    surplus_by_period = defaultdict(list)
    movements_by_period = defaultdict(list)
    collection_totals = collection_summaries(period_names) if summary_only else {}
    historical_differences = historical_difference_counts(period_names) if summary_only else {}
    if period_names:
        rows = [] if summary_only else frappe.get_all(
            "CN Collection Row",
            filters={"parent": ["in", period_names]},
            fields=[
                "parent", "row_key", "client_number", "employee_number", "client_name", "national_id", "loan_number",
                "installment_number", "expected_usd", "deducted_usd",
                "expected_nio", "deducted_nio", "applied_usd", "complementary_usd", "remitted_usd",
                "fx_variance_usd", "rounding_adjustment_usd", "deduction_status", "deduction_match_note", "application_status",
                "comments", "application_comment", "first_exception_comment",
                "inherited_exception_comment", "application_reference",
                "remittance_detail",
            ],
            order_by="parent asc, idx asc",
            limit_page_length=_row_limit(20000, full_export),
        )
        for row in rows:
            row["collection_shortfall_usd"] = collection_shortfall_usd(row)
            row["deduction_pending_reason"] = deduction_pending_type(row)
            rows_by_period[row.parent].append(row)
        historical_rows = [] if summary_only or not frappe.has_permission("CN Accounting Import", "read") else frappe.get_all(
            "CN Source Row",
            filters={
                "historical_period": ["in", period_names],
                "event_type": "Aplicacion", "effective": 1,
            },
            fields=[
                "name", "parent", "source_row", "historical_period", "event_date",
                "reference", "voucher", "accounting_entry", "receipt", "client_number", "employee_number", "client_name", "national_id",
                "loan_number", "installment_number", "amount", "currency", "application_adjustment_usd", "net_applied_usd", "application_adjustment_status",
                "historical_remitted_usd", "historical_balance_usd",
                "historical_detail", "deposit_match_status", "deposit_match_reason",
            ],
            order_by="event_date asc, idx asc",
            limit_page_length=_row_limit(30000, full_export),
        )
        if historical_rows:
            allowed_imports = readable_imports({row.parent for row in historical_rows})
            historical_rows = [row for row in historical_rows if row.parent in allowed_imports]
        annotate_application_exceptions(historical_rows)
        for row in historical_rows:
            historical_rows_by_period[row.historical_period].append(row)
        exceptions = frappe.get_list(
            "CN Reconciliation Exception",
            filters={
                "period": ["in", period_names],
                "status": ["in", ["Abierta", "En revision"]],
            },
            fields=[
                "name", "period", "exception_type", "client_number",
                "loan_number", "amount_usd", "description", "status",
                "cause_category", "assigned_to", "next_action", "commitment_date",
                "evidence_file", "external_reference",
            ] if not summary_only else ["name", "period", "amount_usd", "next_action", "commitment_date"],
            limit_page_length=_row_limit(10000, full_export),
        ) if frappe.has_permission("CN Reconciliation Exception", "read") else []
        for item in exceptions:
            exceptions_by_period[item.period].append(item)
        surpluses = frappe.get_list(
            "CN Complementary Item",
            filters={"period": ["in", period_names], "docstatus": 1, "category": ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]]},
            fields=[
                "name", "period", "reference as deposit_reference", "amount_usd",
                "reason_type", "description as explanation", "result",
            ] if not summary_only else ["name", "period", "amount_usd", "result"],
            limit_page_length=_row_limit(10000, full_export),
        ) if frappe.has_permission("CN Complementary Item", "read") else []
        for item in surpluses:
            surplus_by_period[item.period].append(item)
        movements = frappe.get_list(
            "CN Complementary Item",
            filters={"category": TOLERANCE_CATEGORY, "docstatus": 1, "period": ["in", period_names], "status": "Vigente"},
            fields=[
                "name", "period", "deposit_reference", "application_source_row",
                "signed_amount_usd", "tolerance_usd", "deposit_usd", "core_applied_usd",
                "posting_date", "description",
            ] if not summary_only else ["name", "period", "signed_amount_usd"],
            order_by="creation desc",
            limit_page_length=_row_limit(20000, full_export),
        ) if frappe.has_permission("CN Complementary Item", "read") else []
        for item in movements:
            movements_by_period[item.period].append(item)

    output = []
    totals = defaultdict(float)
    for period in periods:
        expected = flt(period.expected_usd)
        deducted = flt(period.deducted_usd)
        applied = flt(period.applied_usd)
        complementary = flt(period.complementary_usd)
        remitted = flt(period.remitted_usd)
        is_historical = period.reconciliation_mode == "Historica"
        fx_variance = max(flt(period.fx_variance_usd), 0)
        period_rows = rows_by_period[period.name]
        worker_gap = sum(
            flt(row.collection_shortfall_usd)
            for row in period_rows
        ) if not is_historical else 0
        pending_detail = sum(
            flt(row.expected_usd)
            for row in period_rows
            if row.collection_shortfall_usd is None
        ) if not is_historical else 0
        aggregate = collection_totals.get(period.name, {})
        if summary_only:
            worker_gap = flt(aggregate.get("worker_gap_usd")) if not is_historical else 0
            pending_detail = flt(aggregate.get("pending_detail_usd")) if not is_historical else 0
        adjustment = flt(period.rounding_adjustment_usd)
        # This is an assignment gap, not a confirmed company receivable: an
        # already received deposit may still lack its per-client detail.
        employer_gap = (flt(aggregate.get("employer_gap_usd")) if summary_only else
                        sum(flt(unassigned_deduction_usd(row)) for row in period_rows)) if not is_historical else 0
        historical_pending = max(applied + adjustment - remitted, 0) if is_historical else 0
        period_surpluses = surplus_by_period[period.name]
        period_movements = movements_by_period[period.name]
        documented_credit = sum(
            flt(item.amount_usd)
            for item in period_surpluses
            if item.result == "Saldo a favor documentado"
        )
        if is_historical:
            control_state = (
                "historico_excepcion" if exceptions_by_period[period.name]
                else "historico_conciliado" if applied > CASH_EPSILON
                and historical_pending <= CASH_EPSILON
                else "historico_parcial" if remitted > CASH_EPSILON
                else "historico_pendiente"
            )
        elif (
            flt(period.exception_count) or worker_gap > CASH_EPSILON
            or flt(aggregate.get("application_difference_count"))
            or any(row.application_status == "Diferencia aplicacion vs deposito" for row in period_rows)
        ):
            control_state = "diferencia"
        elif pending_detail > CASH_EPSILON:
            control_state = "pendiente_detalle"
        elif (
            deducted > CASH_EPSILON
            and employer_gap <= CASH_EPSILON
            and applied + complementary + fx_variance + max(adjustment, 0) + CASH_EPSILON >= deducted
        ):
            control_state = "conciliado"
        elif remitted > CASH_EPSILON:
            control_state = "parcial"
        else:
            control_state = "en_transito"
        record = dict(period)
        record.update(
            {
                "month": str(period.payroll_month)[:7],
                "employer_name": employer_names.get(period.employer, period.employer),
                "worker_gap_usd": money_float(worker_gap),
                "pending_detail_usd": money_float(pending_detail),
                "employer_gap_usd": money_float(employer_gap),
                "historical_pending_usd": money_float(historical_pending),
                "rounding_adjustment_usd": money_float(adjustment),
                "rounding_movement_abs_usd": money_float(
                    sum(abs(flt(item.signed_amount_usd)) for item in period_movements)
                ),
                "inferred_deduction_usd": money_float(
                    deducted if period.deduction_basis == "Depósito coincidente" else 0
                ),
                "documented_credit_usd": money_float(documented_credit),
                "unclassified_deposit_usd": 0,
                "control_state": control_state,
                "rounding_movement_count": len(period_movements),
                "rows": period_rows,
                "historical_rows": historical_rows_by_period[period.name],
                "exceptions": exceptions_by_period[period.name],
                "surpluses": period_surpluses,
                "rounding_movements": period_movements,
            }
        )
        if summary_only:
            record["difference_count"] = cint(aggregate.get("difference_count")) + historical_differences[period.name]
        output.append(record)
        for field in (
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "rounding_adjustment_usd",
            "remitted_usd", "worker_gap_usd", "pending_detail_usd", "employer_gap_usd",
            "historical_pending_usd",
            "inferred_deduction_usd",
            "rounding_movement_abs_usd",
            "documented_credit_usd",
        ):
            totals[field] += flt(record.get(field))

    if detail_period:
        return {"periods": output}

    # A deposit can fund several periods. Flag every related cell without
    # adding the same open cash twice to the headline total.
    reference_periods = defaultdict(set)
    target_links = []
    for record in output:
        for row in record["rows"]:
            if row.application_reference:
                reference_periods[row.application_reference].add(record["name"])
    if period_names:
        if summary_only:
            for row in frappe.get_all(
                "CN Collection Row", filters={"parent": ["in", period_names], "application_reference": ["is", "set"]},
                fields=["parent", "application_reference"], distinct=True, limit_page_length=0,
            ):
                if row.application_reference:
                    reference_periods[row.application_reference].add(row.parent)
        for application in frappe.get_all(
            "CN Source Row",
            filters={
                "event_type": "Aplicacion", "effective": 1,
                "collection_period": ["in", period_names],
            },
            fields=["reference", "collection_period", "application_allocation_detail"],
        ):
            if application.reference:
                reference_periods[application.reference].add(application.collection_period)
                for link in json.loads(application.application_allocation_detail or "[]"):
                    if link.get("period") in period_names:
                        reference_periods[application.reference].add(link["period"])
        target_links = frappe.get_all(
            "CN Remittance Target",
            filters={"period": ["in", period_names]},
            fields=["parent", "period"], limit_page_length=_row_limit(100000, full_export),
        )
        if target_links:
            deposit_references = {
                allocation.name: allocation.deposit_reference
                for allocation in frappe.get_all(
                    "CN Remittance Allocation",
                    filters={
                        "name": ["in", list({row.parent for row in target_links})],
                        "docstatus": 1,
                    },
                    fields=["name", "deposit_reference"],
                    limit_page_length=_row_limit(100000, full_export),
                )
            }
            for target in target_links:
                reference = deposit_references.get(target.parent)
                if reference:
                    reference_periods[reference].add(target.period)

    deposits = []
    unassigned_historical_applications = []
    unassigned_operational_applications = []
    unassigned_surpluses = []
    related_deposits = []
    if frappe.has_permission("CN Accounting Import", "read"):
        source_imports = frappe.get_list(
            "CN Accounting Import",
            filters={
                "status": ["in", ["Importado", "Importado con excepciones"]],
                **({"employer": employer} if employer else {}),
            },
            fields=["name", "employer", "historical_backfill", "historical_period"],
            limit_page_length=_row_limit(3000, full_export),
        )
        import_names = [item.name for item in source_imports]
        if import_names:
            if not employer or full_export:
                unassigned_applications = frappe.get_all(
                    "CN Source Row",
                    filters={
                        "parent": ["in", import_names],
                        "event_type": "Aplicacion", "effective": 1,
                        "historical_period": ["is", "not set"],
                        "collection_period": ["is", "not set"],
                        **_year_filter("event_date", year),
                    },
                    fields=[
                        "name", "parent", "source_row", "event_date", "reference", "client_name", "loan_number",
                        "client_number", "accounting_entry", "receipt",
                        "amount", "amount_usd", "currency", "match_reason", "application_adjustment_usd", "net_applied_usd",
                        "processing_route",
                    ],
                    order_by="event_date asc, idx asc",
                    limit_page_length=_row_limit(10000, full_export),
                )
                imports_by_name = {item.name: item for item in source_imports}
                for row in unassigned_applications:
                    row["employer"] = imports_by_name[row.parent].employer
                    if employer and row.employer != employer:
                        continue
                    if net_application_amount(row) <= 0:
                        continue
                    target = (
                        unassigned_historical_applications
                        if _unassigned_application_is_historical(
                            row, imports_by_name[row.parent]
                        ) else unassigned_operational_applications
                    )
                    target.append(row)
            related_deposits = frappe.get_all(
                "CN Source Row",
                filters={
                    "parent": ["in", import_names],
                    "event_type": "Deposito",
                    "effective": 1,
                    "unclassified_usd": [">", CASH_EPSILON],
                    "reference": ["in", list(reference_periods)],
                },
                fields=[
                    "reference", "voucher", "currency", "amount",
                    "unclassified_usd", "allocation_detail",
                ],
            ) if reference_periods else []
            periods_by_name = {record["name"]: record for record in output}
            related_deposits = [
                deposit for deposit in related_deposits
                if any(
                    entry.get("periodo") in periods_by_name
                    for entry in json.loads(deposit.allocation_detail or "[]")
                )
            ]
            for deposit in related_deposits:
                totals["unclassified_deposit_usd"] += flt(deposit.unclassified_usd)
                related_periods = {
                    entry.get("periodo")
                    for entry in json.loads(deposit.allocation_detail or "[]")
                    if entry.get("periodo") in periods_by_name
                }
                if len(related_periods) == 1:
                    record = periods_by_name[next(iter(related_periods))]
                    record["unclassified_deposit_usd"] += flt(deposit.unclassified_usd)
            if not employer:
                deposits = frappe.get_all(
                    "CN Source Row",
                    filters={
                        "parent": ["in", import_names],
                        "event_type": "Deposito",
                        "effective": 1,
                        "unallocated_usd": [">", CASH_EPSILON],
                        **_year_filter("event_date", year),
                    },
                    fields=[
                        "parent", "reference", "voucher", "event_date",
                        "employer_text", "currency", "amount", "allocated_usd",
                        "unallocated_usd", "justified_surplus_usd",
                        "unclassified_usd", "allocation_reason", "allocation_detail",
                    ],
                    order_by="event_date desc",
                    limit_page_length=_row_limit(1000, full_export),
                )
    registered = []
    if frappe.has_permission("CN Remittance Allocation", "read"):
        manual_filters = {
            "docstatus": 1,
            **_year_filter("deposit_date", year),
        }
        if employer:
            manual_filters["employer"] = employer
        registered = frappe.get_list(
            "CN Remittance Allocation",
            filters=manual_filters,
            fields=[
                "name", "employer", "bank_account", "deposit_reference", "deposit_voucher",
                "deposit_date", "deposit_currency", "deposit_amount",
                "amount_usd", "detail_file", "detail_count", "detail_status",
                "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                "unclassified_usd", "allocation_detail", "result",
            ],
            limit_page_length=_row_limit(10000, full_export),
        )
        if period_names and target_links:
            # A December period may be funded by a registered January deposit.
            # Include its explicit targets even though the deposit year differs.
            known = {item.name for item in registered}
            target_parents = list({target.parent for target in target_links} - known)
            for offset in range(0, len(target_parents), 500):
                related_filters = {
                    "name": ["in", target_parents[offset:offset + 500]],
                    "docstatus": 1,
                }
                if employer:
                    related_filters["employer"] = employer
                registered.extend(frappe.get_list(
                    "CN Remittance Allocation",
                    filters=related_filters,
                    fields=[
                        "name", "employer", "deposit_reference", "deposit_voucher",
                        "deposit_date", "deposit_currency", "deposit_amount",
                        "amount_usd", "detail_file", "detail_count", "detail_status",
                        "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                        "unclassified_usd", "allocation_detail", "result",
                    ],
                    limit_page_length=0,
                ))
        if period_names:
            # A December payroll may have its client detail or automatic cash
            # split in a deposit dated the following year, without a manual
            # CN Remittance Target. Keep those linked deposits visible.
            known = {item.name for item in registered}
            for offset in range(0, len(period_names), 500):
                related_filters = {
                    "docstatus": 1,
                    "name": ["in", deposit_names_for_periods(period_names[offset:offset + 500]) or [""]],
                }
                if employer:
                    related_filters["employer"] = employer
                for item in frappe.get_list(
                    "CN Remittance Allocation", filters=related_filters,
                    fields=[
                        "name", "employer", "deposit_reference", "deposit_voucher",
                        "deposit_date", "deposit_currency", "deposit_amount",
                        "amount_usd", "detail_file", "detail_count", "detail_status",
                        "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                        "unclassified_usd", "allocation_detail", "result",
                    ], limit_page_length=0,
                ):
                    if item.name not in known:
                        registered.append(item)
                        known.add(item.name)
            visible_employers = sorted({period.employer for period in periods if period.employer})
            if visible_employers and year is not None:
                next_year_filters = {
                    "docstatus": 1,
                    "employer": ["in", visible_employers],
                    "deposit_date": ["between", [f"{year + 1}-01-01", f"{year + 1}-12-31"]],
                }
                for item in frappe.get_list(
                    "CN Remittance Allocation", filters=next_year_filters,
                    fields=[
                        "name", "employer", "deposit_reference", "deposit_voucher",
                        "deposit_date", "deposit_currency", "deposit_amount",
                        "amount_usd", "detail_file", "detail_count", "detail_status",
                        "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                        "unclassified_usd", "allocation_detail", "result",
                    ], limit_page_length=_row_limit(10000, full_export),
                ):
                    if item.name in known:
                        continue
                    try:
                        links = json.loads(item.allocation_detail or "[]")
                    except (TypeError, ValueError):
                        links = []
                    if any(link.get("periodo") in period_names for link in links):
                        registered.append(item)
                        known.add(item.name)
        attach_periods(registered)
        imported_keys = {
            (row.reference, row.voucher, row.currency, money_float(row.amount))
            for row in [*deposits, *related_deposits]
        }
        period_records = {record["name"]: record for record in output}
        for item in registered:
            if flt(item.unallocated_usd) <= CASH_EPSILON:
                continue
            key = (
                item.deposit_reference, item.deposit_voucher,
                item.deposit_currency, money_float(item.deposit_amount),
            )
            already_in_imports = key in imported_keys
            justified = flt(item.justified_surplus_usd)
            unclassified = flt(item.unclassified_usd)
            deposits.append({
                "parent": item.name,
                "source_doctype": "CN Remittance Allocation",
                "reference": item.deposit_reference,
                "voucher": item.deposit_voucher,
                "event_date": item.deposit_date,
                "employer_text": item.employer,
                "employer": item.employer,
                "currency": item.deposit_currency,
                "amount": item.deposit_amount,
                "amount_usd": item.amount_usd,
                "allocated_usd": item.allocated_usd,
                "unallocated_usd": item.unallocated_usd,
                "justified_surplus_usd": justified,
                "unclassified_usd": unclassified,
                "allocation_detail": item.allocation_detail or "[]",
            })
            if not already_in_imports:
                totals["unclassified_deposit_usd"] += unclassified
                related_periods = {
                    entry.get("periodo") for entry in json.loads(item.allocation_detail or "[]")
                    if entry.get("periodo") in period_records
                }
                if len(related_periods) == 1:
                    record = period_records[next(iter(related_periods))]
                    record["unclassified_deposit_usd"] += unclassified
        if registered:
            no_period_credit = frappe.get_list(
                "CN Complementary Item",
                filters={
                    "category": ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]],
                    "docstatus": 1, "result": "Saldo a favor documentado",
                    "period": ["is", "not set"],
                    "registered_deposit": ["in", [item.name for item in registered]],
                },
                fields=[
                    "name", "employer", "reference as deposit_reference", "amount_usd",
                    "reason_type", "description as explanation", "result",
                ],
                limit_page_length=_row_limit(10000, full_export),
            ) if frappe.has_permission("CN Complementary Item", "read") else []
            unassigned_surpluses.extend(no_period_credit)
            totals["documented_credit_usd"] += sum(
                flt(item.amount_usd) for item in no_period_credit
            )
        registered_keys = {
            (item.deposit_reference, item.deposit_voucher,
             item.deposit_currency, money_float(item.deposit_amount))
            for item in registered
        }
        deposits = [
            row for row in deposits
            if row.get("source_doctype") == "CN Remittance Allocation"
            or (row.reference, row.voucher, row.currency, money_float(row.amount))
            not in registered_keys
        ]
    missing_employers = {
        item.employer for item in registered
        if item.employer and item.employer not in employer_names
    }
    if missing_employers:
        employer_names.update({
            item.name: item.employer_name
            for item in frappe.get_all(
                "CN Employer",
                filters={"name": ["in", list(missing_employers)]},
                fields=["name", "employer_name"],
            )
        })
    work_items = _build_work_items(
        output, deposits, registered, target_links,
        unassigned_historical_applications, unassigned_operational_applications,
        employer_names,
    )
    from credinomina_reconciliation.follow_up_queue import load_follow_up, merge_follow_up, group_work_cases
    if not detail_period and detail_section in (None, "work_items", "work_overview"):
        work_items = merge_follow_up(work_items, load_follow_up(year, employer))
    work_items = group_work_cases(work_items)
    if detail_section == "work_overview":
        return {"work_scope": "Todos", "work_items": work_items[:100], "work_item_count": len(work_items),
                "work_action_count": sum(item.get('action_count', 1) for item in work_items),
                "overdue_count": sum(item["priority"] == 0 for item in work_items),
                "open_deposits": deposits[:100], "open_deposit_count": len(deposits),
                "unassigned_historical_applications": unassigned_historical_applications[:100],
                "unassigned_historical_count": len(unassigned_historical_applications)}
    if detail_section:
        return {detail_section: {
            "open_deposits": deposits,
            "unassigned_historical_applications": unassigned_historical_applications,
            "work_items": work_items,
        }[detail_section]}
    if summary_only:
        for period in output:
            for field in ("rows", "historical_rows", "exceptions", "surpluses", "rounding_movements", "control_cut_summary"):
                period.pop(field, None)
            period["detail_loaded"] = False
    return {
        "year": year if year is not None else "Todos",
        "available_years": _available_years(employer),
        "can_create_exception": bool(frappe.has_permission("CN Reconciliation Exception", "create")),
        "periods": output,
        "cash_deposits": get_cash_deposits(year, employer, include_details=False, deposits=[
            item for item in registered if year is None or getdate(item.deposit_date).year == year
        ]) if summary_only else get_cash_deposits(year, employer),
        "totals": {key: money_float(value) for key, value in totals.items()},
        "open_deposit_count": len(deposits),
        "unassigned_historical_count": len(unassigned_historical_applications),
        "work_item_count": len(work_items),
        "work_action_count": sum(item.get('action_count', 1) for item in work_items),
        "overdue_count": sum(item["priority"] == 0 for item in work_items),
        "open_deposits": deposits[:100] if summary_only else deposits,
        "unassigned_historical_applications": unassigned_historical_applications[:100] if summary_only else unassigned_historical_applications,
        "unassigned_operational_applications": unassigned_operational_applications[:100] if summary_only else unassigned_operational_applications,
        "unassigned_surpluses": unassigned_surpluses,
        "work_items": work_items[:100] if summary_only else work_items,
    }


def _build_work_items(
    periods, deposits, registered, target_links, historical_unassigned,
    operational_unassigned, employer_names, *, as_of=None,
):
    """Actionable evidence, never an inferred receivable or automatic match."""
    items = []
    period_by_name = {period["name"]: period for period in periods}
    registered_by_name = {item.name: item for item in registered}
    linked_context_by_deposit = {}
    targets_by_deposit = defaultdict(set)
    for target in target_links:
        if target.period in period_by_name:
            targets_by_deposit[target.parent].add(target.period)

    def period_label(period):
        month = period.get("month") or str(period.get("payroll_month") or "")[:7]
        if period.get("reconciliation_mode") == "Historica":
            scope = period.get("historical_scope")
            if scope == "Fecha exacta":
                return f"{month} · {display_date(period.get('historical_application_date')) or 'fecha exacta'}"
            if scope == "Rango de fechas":
                start = display_date(period.get("historical_start_date")) or "?"
                end = display_date(period.get("historical_end_date")) or "?"
                return f"{month} · {start}–{end}"
            return f"{month} · histórico"
        cycle = period.get('collection_cycle') or 'mensual'
        if cycle == "Fecha exacta":
            cycle += f" · {display_date(period.get('cutoff_date'))}"
        return f"{month} · {cycle}"

    def add(priority, kind, summary, next_action, *, period=None, employer=None,
            amount_usd=None, count=None, due_date=None, target_doctype=None,
            target_name=None):
        period_record = period_by_name.get(period)
        employer = employer or (period_record or {}).get("employer")
        items.append({
            "priority": priority,
            "kind": kind,
            "employer": employer,
            "employer_name": (period_record or {}).get("employer_name")
            or employer_names.get(employer, employer) or "Sin empresa confirmada",
            "period": period,
            "period_label": period_label(period_record) if period_record else "Sin período",
            "control_cut_on": (period_record or {}).get("control_cut_on"),
            "summary": summary,
            "next_action": next_action,
            "amount_usd": money_float(amount_usd) if amount_usd is not None else None,
            "count": count,
            "due_date": str(due_date)[:10] if due_date else None,
            "target_doctype": target_doctype,
            "target_name": target_name,
        })

    for period in periods:
        name = period["name"]
        target = {"period": name, "target_doctype": "CN Reconciliation Period",
                  "target_name": name}
        if period.get("reconciliation_mode") == "Historica":
            pending = flt(period.get("historical_pending_usd"))
            if pending > CASH_EPSILON:
                add(3, "historical_cash", "Aplicaciones históricas sin depósito asignado",
                    "Ubicar el depósito y vincularlo con estas aplicaciones.",
                    amount_usd=pending, **target)
        else:
            missing = flt(period.get("pending_detail_usd"))
            if missing > CASH_EPSILON:
                action = ("Importar y validar el detalle recibido de la empresa."
                          if period.get("employer_response_file") else
                          "Solicitar e importar el detalle de deducción de la empresa.")
                add(2, "company_detail", "Detalle de empresa pendiente o por aclarar",
                    action, amount_usd=missing, **target)
            if period.get("deduction_basis") == "Depósito coincidente":
                add(2, "inferred_deduction", "Deducción inferida del depósito",
                    "Obtener el detalle de planilla para confirmar cada cliente.",
                    amount_usd=period.get("inferred_deduction_usd"), **target)
            employee_gap = flt(period.get("worker_gap_usd"))
            if employee_gap > CASH_EPSILON:
                add(3, "employee_shortfall", "Cuotas no deducidas según detalle",
                    "Revisar motivo y seguimiento de las cuotas no deducidas.",
                    amount_usd=employee_gap, **target)
            employer_gap = flt(period.get("employer_gap_usd"))
            if employer_gap > CASH_EPSILON:
                add(2, "unlinked_remittance", "Deducción sin depósito asignado",
                    "Ubicar el depósito o vincular el depósito; no es CxC confirmada.",
                    amount_usd=employer_gap, **target)

        difference_rows = [
            row for row in period.get("rows", [])
            if row.application_status in {
                "Diferencia aplicacion vs deposito", "Diferencia cambiaria en revision",
            }
        ]
        historical_differences = [
            row for row in period.get("historical_rows", [])
            if row.deposit_match_status in {
                "Diferencia de importe", "Falta tipo de cambio", "Ambiguo",
            }
        ]
        difference_count = period.get("difference_count", len(difference_rows) + len(historical_differences))
        if difference_count:
            add(1, "difference", "Diferencias de conciliación por revisar",
                "Abrir el período y resolver las filas señaladas.",
                count=difference_count, **target)
        for exception in period.get("exceptions", []):
            due = exception.commitment_date
            if due and as_of is None:
                as_of = now_datetime().date()
            if due and str(due)[:10] < as_of.isoformat():
                add(0, "overdue_exception", "Excepción con compromiso vencido",
                    exception.next_action or "Registrar la siguiente gestión y actualizar el compromiso.",
                    period=name, amount_usd=exception.amount_usd,
                    due_date=due, target_doctype="CN Reconciliation Exception",
                    target_name=exception.name)

    detail_pending_deposits = set()
    for remittance in registered:
        links = targets_by_deposit.get(remittance.name, set()).copy()
        links.update(name for name in selected_periods(remittance) if name in period_by_name)
        try:
            allocation_entries = json.loads(remittance.allocation_detail or "[]")
        except (TypeError, ValueError):
            allocation_entries = []
        links.update(
            entry.get("periodo") for entry in allocation_entries
            if entry.get("periodo") in period_by_name
        )
        linked_period = next(iter(links)) if len(links) == 1 else None
        period_text = (
            ", ".join(period_label(period_by_name[name]) for name in sorted(links))
            if len(links) > 1 else None
        )
        linked_context_by_deposit[remittance.name] = (linked_period, period_text)
        # Explicit destinations plus documented surplus can cover the full
        # deposit without a spreadsheet; do not request one as a false gap.
        covered_without_detail = (
            flt(remittance.allocated_usd) + flt(remittance.justified_surplus_usd)
            >= flt(remittance.amount_usd) - CASH_EPSILON
            and flt(remittance.unclassified_usd) <= CASH_EPSILON
        )
        review_statuses = {
            "Revisar filas", "Detalle supera depósito",
            "Importar detalle actualizado", "Parcial; saldo sin detalle",
        }
        if remittance.result == "Revisar destinos":
            detail_pending_deposits.add(remittance.name)
            item_count = len(items)
            add(1, "review_targets",
                f"Depósito {remittance.deposit_reference} con destinos por revisar",
                "Corregir los destinos manuales inválidos y volver a conciliar.",
                period=linked_period, employer=remittance.employer,
                target_doctype="CN Remittance Allocation", target_name=remittance.name)
            if period_text:
                items[item_count]["period_label"] = period_text
        elif (remittance.detail_status in review_statuses
              and not (remittance.detail_status == "Parcial; saldo sin detalle"
                       and covered_without_detail)):
            detail_pending_deposits.add(remittance.name)
            item_count = len(items)
            add(1, "review_deposit_detail",
                f"Depósito {remittance.deposit_reference} con detalle por revisar",
                "Corregir las filas o importar el detalle actualizado antes de distribuir el saldo.",
                period=linked_period, employer=remittance.employer,
                amount_usd=remittance.amount_usd,
                target_doctype="CN Remittance Allocation", target_name=remittance.name)
            if period_text:
                items[item_count]["period_label"] = period_text
        elif (flt(remittance.amount_usd) > CASH_EPSILON
                and not int(remittance.detail_count or 0)
                and remittance.detail_status not in {
                    "Distribución manual", "Distribución manual; excedente documentado",
                }
                and not covered_without_detail):
            detail_pending_deposits.add(remittance.name)
            item_count = len(items)
            add(2, "deposit_detail", f"Depósito {remittance.deposit_reference} sin detalle por cliente",
                "Importar el archivo adjunto de pagos por cliente." if remittance.detail_file
                else "Solicitar y cargar el detalle del depósito por cliente.",
                period=linked_period, employer=remittance.employer,
                amount_usd=remittance.amount_usd,
                target_doctype="CN Remittance Allocation", target_name=remittance.name)
            if period_text:
                items[item_count]["period_label"] = period_text

    for deposit in deposits:
        if deposit.get("parent") in detail_pending_deposits:
            # Request the detail first; the same deposit needs only one task.
            continue
        unclassified = flt(deposit.get("unclassified_usd"))
        if unclassified <= CASH_EPSILON:
            continue
        if deposit.get("source_doctype") == "CN Remittance Allocation":
            remittance = registered_by_name.get(deposit["parent"])
            linked_period, period_text = linked_context_by_deposit.get(
                deposit["parent"], (None, None)
            )
            item_count = len(items)
            add(2, "unassigned_deposit", f"Depósito {deposit.get('reference') or ''} sin distribuir",
                "Identificar su destino o documentar el saldo a favor.",
                period=linked_period, employer=(remittance.employer if remittance else deposit.get("employer")),
                amount_usd=unclassified,
                target_doctype="CN Remittance Allocation", target_name=deposit["parent"])
            if period_text:
                items[item_count]["period_label"] = period_text

    # The bank file also contains non-convenio and operational deposits. Only a
    # review task is justified until someone confirms a convenio relationship.
    raw_by_import = defaultdict(list)
    for deposit in deposits:
        if deposit.get("source_doctype") != "CN Remittance Allocation" \
                and flt(deposit.get("unclassified_usd")) > CASH_EPSILON:
            raw_by_import[deposit["parent"]].append(deposit)
    for source, rows in raw_by_import.items():
        add(4, "classify_bank", "Depósitos bancarios sin clasificar",
            "Verificar si son de convenio, de otro cliente u operativos.",
            count=len(rows), amount_usd=sum(flt(row.unclassified_usd) for row in rows),
            target_doctype="CN Accounting Import", target_name=source)

    for label, rows in (
        ("historical_application", historical_unassigned),
        ("operational_application", operational_unassigned),
    ):
        by_import = defaultdict(list)
        for row in rows:
            by_import[row.parent].append(row)
        for source, unmatched in by_import.items():
            companies = {row.get('employer') for row in unmatched if row.get('employer')}
            confirmed_employer = next(iter(companies)) if len(companies) == 1 else None
            known_usd = [
                net_application_amount(row)
                for row in unmatched if row.currency == "USD"
            ]
            add(1, label, "Aplicaciones sin período enlazado",
                "Identificar empresa, cliente y período; revisar el cruce en la importación.",
                employer=confirmed_employer,
                count=len(unmatched),
                amount_usd=sum(known_usd) if len(known_usd) == len(unmatched) else None,
                target_doctype="CN Accounting Import", target_name=source)

    return sorted(items, key=lambda item: (
        item["priority"], item["due_date"] or "9999-12-31",
        item["employer_name"], item["period_label"], item["summary"],
    ))
