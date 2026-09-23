from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from credinomina_reconciliation.aging import employee_receivable_usd


def execute(filters=None):
    filters = frappe._dict(filters or {})
    conditions = {}
    for fieldname in ("employer", "status", "reconciliation_mode"):
        if filters.get(fieldname):
            conditions[fieldname] = filters[fieldname]
    if filters.from_month and filters.to_month:
        conditions["payroll_month"] = ["between", [filters.from_month, filters.to_month]]
    elif filters.from_month:
        conditions["payroll_month"] = [">=", filters.from_month]
    elif filters.to_month:
        conditions["payroll_month"] = ["<=", filters.to_month]

    data = frappe.get_all(
        "CN Reconciliation Period",
        filters=conditions,
        fields=[
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "historical_scope", "historical_application_date", "historical_start_date", "historical_end_date", "cutoff_date", "remittance_due_date", "status", "deduction_basis", "deduction_recognition_reference",
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "remitted_usd", "fx_variance_usd", "rounding_adjustment_usd",
            "expected_nio", "deducted_nio", "applied_nio", "remitted_nio", "exception_count",
        ],
        order_by="payroll_month desc, employer asc",
        limit_page_length=10000,
    )
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
    credit_by_period = {}
    if data:
        for item in frappe.get_all(
            "CN Deposit Surplus",
            filters={
                "period": ["in", [row.name for row in data]],
                "docstatus": 1,
                "result": "Saldo a favor documentado",
            },
            fields=["period", "amount_usd"],
        ):
            credit_by_period[item.period] = (
                credit_by_period.get(item.period, 0) + flt(item.amount_usd)
            )
    employee_by_period = defaultdict(lambda: [0.0, 0.0])
    operational_names = [
        row.name for row in data if row.reconciliation_mode != "Historica"
    ]
    if operational_names:
        for item in frappe.get_all(
            "CN Collection Row",
            filters={"parent": ["in", operational_names]},
            fields=[
                "parent", "expected_usd", "expected_nio", "deducted_usd",
                "deduction_status",
            ],
        ):
            amount = employee_receivable_usd(item)
            if amount is None:
                continue
            employee_by_period[item.parent][0] += amount
            if flt(item.expected_usd) > 0:
                employee_by_period[item.parent][1] += (
                    amount * flt(item.expected_nio) / flt(item.expected_usd)
                )
    for row in data:
        row["usd_currency"] = "USD"
        row["nio_currency"] = "NIO"
        if row.reconciliation_mode == "Historica":
            row["historical_label"] = (
                f"Fecha {row.historical_application_date}"
                if row.historical_scope == "Fecha exacta"
                else f"{row.historical_start_date} – {row.historical_end_date}"
                if row.historical_scope == "Rango de fechas"
                else "Mensual"
            )
            row["employee_shortfall_usd"] = 0
            row["employer_receivable_usd"] = 0
            row["employee_shortfall_nio"] = 0
            row["employer_receivable_nio"] = 0
            row["historical_pending_usd"] = max(flt(row.applied_usd) + flt(row.rounding_adjustment_usd) - flt(row.remitted_usd), 0)
            row["company_credit_usd"] = credit_by_period.get(row.name, 0)
            continue
        rate = flt(row.expected_nio) / flt(row.expected_usd) if flt(row.expected_usd) else 0
        row["employee_shortfall_usd"] = round(employee_by_period[row.name][0], 4)
        row["employer_receivable_usd"] = max(
            flt(row.deducted_usd) - flt(row.remitted_usd) - max(flt(row.fx_variance_usd), 0)
            - max(-flt(row.rounding_adjustment_usd), 0), 0
        )
        row["employee_shortfall_nio"] = round(employee_by_period[row.name][1], 4)
        row["employer_receivable_nio"] = max(
            flt(row.deducted_nio) - flt(row.remitted_nio) - (
                max(flt(row.fx_variance_usd), 0) + max(-flt(row.rounding_adjustment_usd), 0)
            ) * rate, 0
        )
        row["company_credit_usd"] = credit_by_period.get(row.name, 0)
        row["historical_pending_usd"] = 0
    return get_columns(), data


def get_columns():
    return [
        {"fieldname": "name", "label": _("Periodo"), "fieldtype": "Link", "options": "CN Reconciliation Period", "width": 155},
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 95},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "reconciliation_mode", "label": _("Modalidad"), "fieldtype": "Data", "width": 100},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 140},
        {"fieldname": "historical_label", "label": _("Corte histórico"), "fieldtype": "Data", "width": 210},
        {"fieldname": "deduction_basis", "label": _("Origen de deducción"), "fieldtype": "Data", "width": 155},
        {"fieldname": "deduction_recognition_reference", "label": _("Depósito para deducción"), "fieldtype": "Data", "width": 155},
        {"fieldname": "cutoff_date", "label": _("Cierre del ciclo"), "fieldtype": "Date", "width": 110},
        {"fieldname": "status", "label": _("Estado"), "fieldtype": "Data", "width": 150},
        {"fieldname": "remittance_due_date", "label": _("Vence remesa"), "fieldtype": "Date", "width": 105},
        {"fieldname": "expected_usd", "label": _("Cobrado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 105},
        {"fieldname": "deducted_usd", "label": _("Deducido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 110},
        {"fieldname": "applied_usd", "label": _("Aplicado al crédito US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "complementary_usd", "label": _("Partida complementaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "remitted_usd", "label": _("Remitido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 115},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 150},
        {"fieldname": "rounding_adjustment_usd", "label": _("Movimiento de conciliación US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 175},
        {"fieldname": "employee_shortfall_usd", "label": _("CxC a empleados US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "employer_receivable_usd", "label": _("Deducido sin remesa asignada US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 225},
        {"fieldname": "historical_pending_usd", "label": _("Aplicación histórica sin depósito US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 185},
        {"fieldname": "company_credit_usd", "label": _("Saldo a favor documentado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 170},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 105},
        {"fieldname": "deducted_nio", "label": _("Deducido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 110},
        {"fieldname": "remitted_nio", "label": _("Remitido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 115},
        {"fieldname": "employee_shortfall_nio", "label": _("CxC a empleados equivalente C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 205},
        {"fieldname": "employer_receivable_nio", "label": _("Deducido sin remesa asignada C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 225},
        {"fieldname": "exception_count", "label": _("Excepciones"), "fieldtype": "Int", "width": 90},
    ]
