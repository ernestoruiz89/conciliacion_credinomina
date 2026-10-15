from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from credinomina_reconciliation.aging import collection_shortfall_usd, unassigned_deduction_usd
from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.rounding import decimal_value, money, money_float
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.period_presentation import financial_position


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

    data = list(records(
        "CN Reconciliation Period",
        filters=conditions,
        fields=[
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "historical_scope", "historical_application_date", "historical_start_date", "historical_end_date", "cutoff_date", "remittance_due_date", "status", "deduction_basis", "deduction_recognition_reference",
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "remitted_usd", "fx_variance_usd", "rounding_adjustment_usd",
            "expected_nio", "deducted_nio", "applied_nio", "remitted_nio", "exception_count",
            "applied_total_usd", "remitted_total_usd", "pending_usd", "status_before_close",
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
    credit_by_period = {}
    if data:
        for item in records(
            "CN Complementary Item",
            filters={
                "category": ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]],
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
    company_by_period = defaultdict(lambda: [0.0, 0.0])
    operational_names = [
        row.name for row in data if row.reconciliation_mode != "Historica"
    ]
    if operational_names:
        for item in child_records(
            "CN Collection Row", operational_names, "CN Reconciliation Period", "collection_rows",
            fields=[
                "parent", "expected_usd", "expected_nio", "deducted_usd",
                "deduction_status", "deducted_nio", "remitted_usd", "remitted_nio",
                "fx_variance_usd", "rounding_adjustment_usd", "applied_usd", "remittance_detail",
            ],
        ):
            amount = collection_shortfall_usd(item)
            if amount is None:
                continue
            employee_by_period[item.parent][0] += amount
            company_by_period[item.parent][0] += unassigned_deduction_usd(item)
            rate = decimal_value(item.expected_nio) / decimal_value(item.expected_usd) if money(item.expected_usd) else 0
            if flt(item.expected_usd) > 0:
                employee_by_period[item.parent][1] += (
                    money_float(
                        decimal_value(amount) * decimal_value(item.expected_nio)
                        / decimal_value(item.expected_usd)
                    )
                )
    for row in data:
        position = financial_position(row)
        row.update(position)
        row['financial_state'] = {'conciliado': 'Conciliado', 'parcial': 'Parcial',
            'en_transito': 'Pendiente', 'excedente': 'Con excedente'}[position['control_state']]
        row['closure_state'] = 'Cerrado' if row.status == 'Cerrado' else 'Abierto'
        row["usd_currency"] = "USD"
        row['complementary_total_usd'] = money_float(money(row.complementary_usd) + money(row.rounding_adjustment_usd))
        row["nio_currency"] = "NIO"
        if row.reconciliation_mode == "Historica":
            row["historical_label"] = (
                f"Fecha {display_date(row.historical_application_date)}"
                if row.historical_scope == "Fecha exacta"
                else f"{display_date(row.historical_start_date)} – {display_date(row.historical_end_date)}"
                if row.historical_scope == "Rango de fechas"
                else "Mensual"
            )
            row["employee_shortfall_usd"] = 0
            row["employer_receivable_usd"] = 0
            row["employee_shortfall_nio"] = 0
            row["employer_receivable_nio"] = 0
            row["historical_pending_usd"] = row['financial_pending_usd']
            row["company_credit_usd"] = money_float(credit_by_period.get(row.name, 0))
            continue
        row["employee_shortfall_usd"] = money_float(employee_by_period[row.name][0])
        row["employer_receivable_usd"] = money_float(company_by_period[row.name][0])
        row["employee_shortfall_nio"] = money_float(employee_by_period[row.name][1])
        row["employer_receivable_nio"] = money_float(company_by_period[row.name][1])
        row["company_credit_usd"] = money_float(credit_by_period.get(row.name, 0))
        row["historical_pending_usd"] = 0
    if filters.get("view") == "Detalle":
        return get_columns(), data
    return get_summary_columns(), data


def get_summary_columns():
    columns = {column["fieldname"]: column for column in get_columns()}
    return [columns[field] for field in (
        "name", "payroll_month", "employer", "financial_state", "closure_state",
        "financial_applied_usd", "financial_covered_usd", "pending_usd", "exception_count",
    )]


def get_columns():
    return [
        {"fieldname": "name", "label": _("Periodo"), "fieldtype": "Link", "options": "CN Reconciliation Period", "width": 155},
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 95},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "reconciliation_mode", "label": _("Modalidad"), "fieldtype": "Data", "width": 100},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 140},
        {"fieldname": "historical_label", "label": _("Corte histórico"), "fieldtype": "Data", "width": 210},
        {"fieldname": "cutoff_date", "label": _("Cierre del ciclo"), "fieldtype": "Date", "width": 110},
        {"fieldname": "financial_state", "label": _("Estado financiero"), "fieldtype": "Data", "width": 150},
        {"fieldname": "closure_state", "label": _("Cierre"), "fieldtype": "Data", "width": 95},
        {"fieldname": "remittance_due_date", "label": _("Vence depósito"), "fieldtype": "Date", "width": 105},
        {"fieldname": "expected_usd", "label": _("Cobranza enviada US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "pending_usd", "label": _("Pendiente US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "financial_applied_usd", "label": _("Aplicado total US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "complementary_total_usd", "label": _("Partidas complementarias US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 175},
        {"fieldname": "financial_covered_usd", "label": _("Asignado total US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 135},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 150},
        {"fieldname": "employee_shortfall_usd", "label": _("Diferencia cobranza/aplicación US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "company_credit_usd", "label": _("Saldo a favor documentado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 170},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 105},
        {"fieldname": "remitted_nio", "label": _("Depositado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 115},
        {"fieldname": "employee_shortfall_nio", "label": _("Diferencia cobranza/aplicación equivalente C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 205},
        {"fieldname": "exception_count", "label": _("Excepciones"), "fieldtype": "Int", "width": 90},
    ]
