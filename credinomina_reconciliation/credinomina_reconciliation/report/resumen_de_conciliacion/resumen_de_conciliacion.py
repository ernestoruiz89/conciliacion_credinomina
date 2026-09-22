import frappe
from frappe import _
from frappe.utils import flt


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
            "name", "employer", "payroll_month", "reconciliation_mode", "collection_cycle", "cutoff_date", "remittance_due_date", "status", "deduction_basis", "deduction_recognition_reference",
            "expected_usd", "deducted_usd", "applied_usd", "complementary_usd", "remitted_usd", "fx_variance_usd", "rounding_adjustment_usd",
            "expected_nio", "deducted_nio", "applied_nio", "remitted_nio", "exception_count",
        ],
        order_by="payroll_month desc, employer asc",
    )
    if filters.collection_cycle:
        data = [
            row for row in data
            if (
                row.collection_cycle
                or ("Mensual" if row.reconciliation_mode != "Historica" else "")
            ) == filters.collection_cycle
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
    for row in data:
        if row.reconciliation_mode == "Historica":
            row["employee_shortfall_usd"] = 0
            row["employer_receivable_usd"] = 0
            row["employee_shortfall_nio"] = 0
            row["employer_receivable_nio"] = 0
            row["historical_pending_usd"] = max(flt(row.applied_usd) + flt(row.rounding_adjustment_usd) - flt(row.remitted_usd), 0)
            row["company_credit_usd"] = credit_by_period.get(row.name, 0)
            continue
        rate = flt(row.expected_nio) / flt(row.expected_usd) if flt(row.expected_usd) else 0
        row["employee_shortfall_usd"] = max(flt(row.expected_usd) - flt(row.deducted_usd), 0)
        row["employer_receivable_usd"] = max(
            flt(row.deducted_usd) - flt(row.remitted_usd) - max(flt(row.fx_variance_usd), 0)
            - max(-flt(row.rounding_adjustment_usd), 0), 0
        )
        row["employee_shortfall_nio"] = max(flt(row.expected_nio) - flt(row.deducted_nio), 0)
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
        {"fieldname": "deduction_basis", "label": _("Origen de deducción"), "fieldtype": "Data", "width": 155},
        {"fieldname": "deduction_recognition_reference", "label": _("Depósito para deducción"), "fieldtype": "Data", "width": 155},
        {"fieldname": "cutoff_date", "label": _("Cierre del ciclo"), "fieldtype": "Date", "width": 110},
        {"fieldname": "status", "label": _("Estado"), "fieldtype": "Data", "width": 150},
        {"fieldname": "remittance_due_date", "label": _("Vence remesa"), "fieldtype": "Date", "width": 105},
        {"fieldname": "expected_usd", "label": _("Cobrado US$"), "fieldtype": "Currency", "options": "USD", "width": 105},
        {"fieldname": "deducted_usd", "label": _("Deducido US$"), "fieldtype": "Currency", "options": "USD", "width": 110},
        {"fieldname": "applied_usd", "label": _("Aplicado al crédito US$"), "fieldtype": "Currency", "options": "USD", "width": 145},
        {"fieldname": "complementary_usd", "label": _("Partida complementaria US$"), "fieldtype": "Currency", "options": "USD", "width": 155},
        {"fieldname": "remitted_usd", "label": _("Remitido US$"), "fieldtype": "Currency", "options": "USD", "width": 115},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "USD", "width": 150},
        {"fieldname": "rounding_adjustment_usd", "label": _("Movimiento de conciliación US$"), "fieldtype": "Currency", "options": "USD", "width": 175},
        {"fieldname": "employee_shortfall_usd", "label": _("Faltante empleado US$"), "fieldtype": "Currency", "options": "USD", "width": 135},
        {"fieldname": "employer_receivable_usd", "label": _("CxC empresa US$"), "fieldtype": "Currency", "options": "USD", "width": 120},
        {"fieldname": "historical_pending_usd", "label": _("Aplicación histórica sin depósito US$"), "fieldtype": "Currency", "options": "USD", "width": 185},
        {"fieldname": "company_credit_usd", "label": _("Saldo a favor documentado US$"), "fieldtype": "Currency", "options": "USD", "width": 170},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "NIO", "width": 105},
        {"fieldname": "deducted_nio", "label": _("Deducido C$"), "fieldtype": "Currency", "options": "NIO", "width": 110},
        {"fieldname": "remitted_nio", "label": _("Remitido C$"), "fieldtype": "Currency", "options": "NIO", "width": 115},
        {"fieldname": "employee_shortfall_nio", "label": _("Faltante empleado C$"), "fieldtype": "Currency", "options": "NIO", "width": 135},
        {"fieldname": "employer_receivable_nio", "label": _("CxC empresa C$"), "fieldtype": "Currency", "options": "NIO", "width": 120},
        {"fieldname": "exception_count", "label": _("Excepciones"), "fieldtype": "Int", "width": 90},
    ]
