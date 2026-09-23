from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt

from credinomina_reconciliation.aging import employee_receivable_usd
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING
from credinomina_reconciliation.rounding import CASH_EPSILON


@frappe.whitelist()
def get_control_data(year=None, employer=None):
    """Permission-scoped, real data for the monthly control matrix."""
    if not frappe.has_permission("CN Reconciliation Period", "read"):
        frappe.throw(_("No tiene permiso para consultar la conciliacion."))
    year = cint(year or date.today().year)
    if not 2000 <= year <= 2100:
        frappe.throw(_("Indique un año valido."))
    filters = {"payroll_month": ["between", [f"{year}-01-01", f"{year}-12-31"]]}
    if employer:
        filters["employer"] = employer
    periods = frappe.get_list(
        "CN Reconciliation Period",
        filters=filters,
        fields=[
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "historical_scope", "historical_application_date", "historical_start_date", "historical_end_date", "cutoff_date", "remittance_due_date", "status", "deduction_basis", "deduction_recognition_reference",
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "rounding_adjustment_usd",
            "remitted_usd", "fx_variance_usd", "exception_count",
        ],
        order_by="employer asc, payroll_month asc",
        limit_page_length=3000,
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
    if period_names:
        rows = frappe.get_all(
            "CN Collection Row",
            filters={"parent": ["in", period_names]},
            fields=[
                "parent", "client_number", "client_name", "loan_number",
                "installment_number", "expected_usd", "deducted_usd",
                "applied_usd", "complementary_usd", "remitted_usd",
                "fx_variance_usd", "rounding_adjustment_usd", "deduction_status", "deduction_match_note", "application_status",
                "comments", "application_comment", "first_exception_comment",
                "inherited_exception_comment", "application_reference",
                "remittance_detail",
            ],
            order_by="parent asc, idx asc",
            limit_page_length=20000,
        )
        for row in rows:
            row["employee_receivable_usd"] = employee_receivable_usd(row)
            rows_by_period[row.parent].append(row)
        historical_rows = frappe.get_all(
            "CN Source Row",
            filters={
                "historical_period": ["in", period_names],
                "event_type": "Aplicacion", "effective": 1,
            },
            fields=[
                "name", "parent", "historical_period", "event_date",
                "reference", "voucher", "client_number", "client_name",
                "loan_number", "installment_number", "amount", "currency",
                "historical_remitted_usd", "historical_balance_usd",
                "historical_detail", "deposit_match_status",
            ],
            order_by="event_date asc, idx asc",
            limit_page_length=30000,
        )
        for row in historical_rows:
            historical_rows_by_period[row.historical_period].append(row)
        exceptions = frappe.get_all(
            "CN Reconciliation Exception",
            filters={
                "period": ["in", period_names],
                "status": ["in", ["Abierta", "En revision"]],
            },
            fields=[
                "name", "period", "exception_type", "client_number",
                "loan_number", "amount_usd", "description", "status",
            ],
            limit_page_length=10000,
        )
        for item in exceptions:
            exceptions_by_period[item.period].append(item)
        surpluses = frappe.get_all(
            "CN Deposit Surplus",
            filters={"period": ["in", period_names], "docstatus": 1},
            fields=[
                "name", "period", "deposit_reference", "amount_usd",
                "reason_type", "explanation", "result",
            ],
            limit_page_length=10000,
        )
        for item in surpluses:
            surplus_by_period[item.period].append(item)
        movements = frappe.get_all(
            "CN Reconciliation Movement",
            filters={"period": ["in", period_names], "status": "Vigente"},
            fields=[
                "name", "period", "deposit_reference", "application_source_row",
                "signed_amount_usd", "tolerance_usd", "deposit_usd", "core_applied_usd",
            ],
            order_by="creation desc",
            limit_page_length=20000,
        )
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
            flt(row.employee_receivable_usd)
            for row in period_rows
        ) if not is_historical else 0
        pending_detail = sum(
            flt(row.expected_usd)
            for row in period_rows
            if row.employee_receivable_usd is None
        ) if not is_historical else 0
        adjustment = flt(period.rounding_adjustment_usd)
        # This is an assignment gap, not a confirmed company receivable: an
        # already received deposit may still lack its per-client detail.
        employer_gap = max(deducted - remitted - fx_variance - max(-adjustment, 0), 0) if not is_historical else 0
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
                "historico_excedente" if period.status == "Historico con excedente"
                else "historico_conciliado" if applied > CASH_EPSILON
                and historical_pending <= CASH_EPSILON
                else "historico_parcial" if remitted > CASH_EPSILON
                else "historico_pendiente"
            )
        elif (
            flt(period.exception_count) or worker_gap > CASH_EPSILON
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
                "worker_gap_usd": round(worker_gap, 4),
                "pending_detail_usd": round(pending_detail, 4),
                "employer_gap_usd": round(employer_gap, 4),
                "historical_pending_usd": round(historical_pending, 4),
                "rounding_adjustment_usd": round(adjustment, 4),
                "rounding_movement_abs_usd": round(
                    sum(abs(flt(item.signed_amount_usd)) for item in period_movements), 4
                ),
                "inferred_deduction_usd": round(
                    deducted if period.deduction_basis == "Depósito coincidente" else 0, 4
                ),
                "documented_credit_usd": round(documented_credit, 4),
                "unclassified_deposit_usd": 0,
                "control_state": control_state,
                "rows": period_rows,
                "historical_rows": historical_rows_by_period[period.name],
                "exceptions": exceptions_by_period[period.name],
                "surpluses": period_surpluses,
                "rounding_movements": period_movements,
            }
        )
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

    # A deposit can fund several periods. Flag every related cell without
    # adding the same open cash twice to the headline total.
    reference_periods = defaultdict(set)
    for record in output:
        for row in record["rows"]:
            if row.application_reference:
                reference_periods[row.application_reference].add(record["name"])
    if period_names:
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
            fields=["parent", "period"], limit_page_length=100000,
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
                    limit_page_length=100000,
                )
            }
            for target in target_links:
                reference = deposit_references.get(target.parent)
                if reference:
                    reference_periods[reference].add(target.period)

    deposits = []
    unassigned_historical_applications = []
    if frappe.has_permission("CN Source Import", "read"):
        historical_imports = frappe.get_list(
            "CN Source Import",
            filters={"historical_backfill": 1},
            pluck="name",
            limit_page_length=3000,
        )
        if historical_imports and not employer:
            unassigned_historical_applications = frappe.get_all(
                "CN Source Row",
                filters={
                    "parent": ["in", historical_imports],
                    "event_type": "Aplicacion", "effective": 1,
                    "historical_period": ["is", "not set"],
                    "event_date": ["between", [f"{year}-01-01", f"{year}-12-31"]],
                },
                fields=[
                    "parent", "event_date", "reference", "loan_number",
                    "amount", "currency", "match_reason", "processing_route",
                ],
                order_by="event_date asc, idx asc",
                limit_page_length=5000,
            )
            unassigned_historical_applications = [
                row for row in unassigned_historical_applications
                if row.processing_route != "Operativa"
            ]
        import_names = frappe.get_list(
            "CN Source Import",
            filters={"source_type": SOURCE_ACCOUNTING},
            pluck="name",
            limit_page_length=3000,
        )
        if import_names:
            related_deposits = frappe.get_all(
                "CN Source Row",
                filters={
                    "parent": ["in", import_names],
                    "event_type": "Deposito",
                    "effective": 1,
                    "unclassified_usd": [">", CASH_EPSILON],
                    "reference": ["in", list(reference_periods)],
                },
                fields=["reference", "unclassified_usd"],
            ) if reference_periods else []
            periods_by_name = {record["name"]: record for record in output}
            for deposit in related_deposits:
                totals["unclassified_deposit_usd"] += flt(deposit.unclassified_usd)
                related_periods = reference_periods[deposit.reference]
                if len(related_periods) == 1:
                    record = periods_by_name[next(iter(related_periods))]
                    record["unclassified_deposit_usd"] += flt(deposit.unclassified_usd)
                    if record["control_state"] == "conciliado":
                        record["control_state"] = "excedente"
            if not employer:
                deposits = frappe.get_all(
                    "CN Source Row",
                    filters={
                        "parent": ["in", import_names],
                        "event_type": "Deposito",
                        "effective": 1,
                        "unallocated_usd": [">", CASH_EPSILON],
                        "event_date": ["between", [f"{year}-01-01", f"{year}-12-31"]],
                    },
                    fields=[
                        "parent", "reference", "voucher", "event_date",
                        "employer_text", "currency", "amount", "allocated_usd",
                        "unallocated_usd", "justified_surplus_usd",
                        "unclassified_usd", "allocation_reason", "allocation_detail",
                    ],
                    order_by="event_date desc",
                    limit_page_length=1000,
                )
    if frappe.has_permission("CN Remittance Allocation", "read"):
        manual_filters = {
            "docstatus": 1,
            "deposit_date": ["between", [f"{year}-01-01", f"{year}-12-31"]],
        }
        if employer:
            manual_filters["employer"] = employer
        registered = frappe.get_list(
            "CN Remittance Allocation",
            filters=manual_filters,
            fields=[
                "name", "employer", "deposit_reference", "deposit_voucher",
                "deposit_date", "deposit_currency", "deposit_amount",
                "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                "unclassified_usd", "allocation_detail", "result",
            ],
            limit_page_length=10000,
        )
        imported_keys = {
            (row.reference, row.voucher, row.currency, round(flt(row.amount), 4))
            for row in deposits
        }
        period_records = {record["name"]: record for record in output}
        for item in registered:
            if flt(item.unallocated_usd) <= CASH_EPSILON:
                continue
            key = (
                item.deposit_reference, item.deposit_voucher,
                item.deposit_currency, round(flt(item.deposit_amount), 4),
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
                "currency": item.deposit_currency,
                "amount": item.deposit_amount,
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
                    if unclassified > CASH_EPSILON and record["control_state"] == "conciliado":
                        record["control_state"] = "excedente"
        if registered:
            no_period_credit = frappe.get_list(
                "CN Deposit Surplus",
                filters={
                    "docstatus": 1, "result": "Saldo a favor documentado",
                    "period": ["is", "not set"],
                    "registered_deposit": ["in", [item.name for item in registered]],
                },
                fields=["amount_usd"],
                limit_page_length=10000,
            ) if frappe.has_permission("CN Deposit Surplus", "read") else []
            totals["documented_credit_usd"] += sum(
                flt(item.amount_usd) for item in no_period_credit
            )
        registered_keys = {
            (item.deposit_reference, item.deposit_voucher,
             item.deposit_currency, round(flt(item.deposit_amount), 4))
            for item in registered
        }
        deposits = [
            row for row in deposits
            if row.get("source_doctype") == "CN Remittance Allocation"
            or (row.reference, row.voucher, row.currency, round(flt(row.amount), 4))
            not in registered_keys
        ]
    return {
        "year": year,
        "periods": output,
        "totals": {key: round(value, 4) for key, value in totals.items()},
        "open_deposits": deposits,
        "unassigned_historical_applications": unassigned_historical_applications,
    }
