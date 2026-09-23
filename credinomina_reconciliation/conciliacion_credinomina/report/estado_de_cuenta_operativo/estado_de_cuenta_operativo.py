import frappe
from frappe import _
from frappe.utils import flt

from credinomina_reconciliation.reconciliation import AMOUNT_TOLERANCE


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    periods = get_periods(filters)
    if not periods:
        return columns, []

    period_map = {row.name: row for row in periods}
    row_filters = {"parent": ["in", list(period_map)]}
    if filters.client_number:
        row_filters["client_number"] = filters.client_number
    if filters.national_id:
        row_filters["national_id"] = filters.national_id
    if filters.loan_number:
        row_filters["loan_number"] = filters.loan_number

    rows = frappe.get_all(
        "CN Collection Row",
        filters=row_filters,
        fields=[
            "parent",
            "client_number",
            "client_name",
            "national_id",
            "loan_number",
            "installment_number",
            "expected_usd",
            "expected_nio",
            "deducted_usd",
            "deducted_nio",
            "applied_usd",
            "applied_nio",
            "complementary_usd",
            "remitted_usd",
            "remitted_nio",
            "fx_variance_usd",
            "rounding_adjustment_usd",
            "deduction_status",
            "application_status",
        ],
        order_by="parent asc, idx asc",
    )
    data = []
    for row in rows:
        period = period_map[row.parent]
        rate = (
            flt(row.expected_nio) / flt(row.expected_usd)
            if flt(row.expected_usd) > AMOUNT_TOLERANCE
            else 0
        )
        classified_usd = flt(row.complementary_usd) + max(flt(row.fx_variance_usd), 0)
        rounding = flt(row.rounding_adjustment_usd)
        classified_nio = classified_usd * rate
        item = frappe._dict(
            {
                **row,
                "usd_currency": "USD",
                "nio_currency": "NIO",
                "period": period.name,
                "payroll_month": period.payroll_month,
                "collection_cycle": period.collection_cycle or "Mensual",
                "deduction_basis": period.deduction_basis or "",
                "employer": period.employer,
                "employee_pending_usd": max(
                    flt(row.expected_usd) - flt(row.deducted_usd), 0
                ),
                "employee_pending_nio": max(
                    flt(row.expected_nio) - flt(row.deducted_nio), 0
                ),
                "pending_core_usd": max(
                    flt(row.deducted_usd) - flt(row.applied_usd) - classified_usd
                    - max(rounding, 0), 0
                ),
                "pending_core_nio": max(
                    flt(row.deducted_nio) - flt(row.applied_nio) - classified_nio
                    - max(rounding, 0) * rate, 0
                ),
                "employer_receivable_usd": max(
                    flt(row.deducted_usd) - flt(row.remitted_usd)
                    - max(flt(row.fx_variance_usd), 0) - max(-rounding, 0), 0
                ),
                "employer_receivable_nio": max(
                    flt(row.deducted_nio) - flt(row.remitted_nio)
                    - (max(flt(row.fx_variance_usd), 0) + max(-rounding, 0)) * rate, 0
                ),
                "operational_status": get_status(row),
            }
        )
        if filters.only_open and item.operational_status == "Conciliado":
            continue
        data.append(item)
    return columns, data


def get_periods(filters):
    conditions = {}
    if filters.employer:
        conditions["employer"] = filters.employer
    if filters.from_month and filters.to_month:
        conditions["payroll_month"] = ["between", [filters.from_month, filters.to_month]]
    elif filters.from_month:
        conditions["payroll_month"] = [">=", filters.from_month]
    elif filters.to_month:
        conditions["payroll_month"] = ["<=", filters.to_month]
    periods = frappe.get_all(
        "CN Reconciliation Period",
        filters=conditions,
        fields=["name", "employer", "payroll_month", "collection_cycle", "deduction_basis"],
        order_by="payroll_month desc",
    )
    return [
        period for period in periods
        if not filters.collection_cycle
        or (period.collection_cycle or "Mensual") == filters.collection_cycle
    ]


def get_status(row):
    status = _base_status(row)
    if status == "Conciliado" and abs(flt(row.rounding_adjustment_usd)) > 0.00005:
        status = "Conciliado con movimiento de conciliación {0:+.4f} US$".format(
            flt(row.rounding_adjustment_usd)
        )
    if row.deduction_status == "Inferida por depósito":
        return status + " · deducción inferida por depósito, sin detalle de planilla"
    return status


def _base_status(row):
    if row.application_status == "Diferencia aplicacion vs deposito":
        return "Diferencia entre aplicación y depósito; requiere revisión"
    if abs(flt(row.fx_variance_usd)) > AMOUNT_TOLERANCE:
        return "Diferencia cambiaria en revisión"
    if row.deduction_status == "Pendiente de detalle":
        return "Pendiente de detalle de la empresa"
    if row.deduction_status in {
        "Deduccion parcial",
        "No deducido",
        "Moneda no coincide",
        "Importes inconsistentes",
        "Importe invalido",
    }:
        return "Pendiente del trabajador / excepcion"
    if row.application_status == "Aplicado y remitido":
        return "Conciliado"
    if row.application_status == "Remitido, aplicacion parcial":
        return "Remitido por la empresa; aplicación parcial en core"
    if row.application_status == "Remesa parcial":
        return "Empresa remitió parcialmente"
    if row.application_status == "Aplicacion parcial":
        return "Aplicación parcial en core; remesa pendiente"
    if row.application_status == "Aplicacion encontrada":
        return "Aplicado en core; empresa por remitir"
    if max(
        flt(row.deducted_usd) - flt(row.applied_usd) - flt(row.complementary_usd),
        flt(row.deducted_nio) - flt(row.applied_nio),
    ) > AMOUNT_TOLERANCE:
        return "Deducido; pendiente de aplicar en core"
    return "Pendiente de conciliacion"


def get_columns():
    return [
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 95},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 140},
        {"fieldname": "deduction_basis", "label": _("Origen de deducción"), "fieldtype": "Data", "width": 160},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "client_number", "label": _("Nro. Cliente"), "fieldtype": "Data", "width": 110},
        {"fieldname": "client_name", "label": _("Cliente"), "fieldtype": "Data", "width": 220},
        {"fieldname": "national_id", "label": _("Cedula"), "fieldtype": "Data", "width": 135},
        {"fieldname": "loan_number", "label": _("Credito"), "fieldtype": "Data", "width": 100},
        {"fieldname": "installment_number", "label": _("Cuota"), "fieldtype": "Data", "width": 70},
        {"fieldname": "expected_usd", "label": _("Cobrado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 105},
        {"fieldname": "deducted_usd", "label": _("Deducido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 110},
        {"fieldname": "applied_usd", "label": _("Aplicado al crédito US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "complementary_usd", "label": _("Partida complementaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "remitted_usd", "label": _("Remitido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 115},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 150},
        {"fieldname": "rounding_adjustment_usd", "label": _("Movimiento de conciliación US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 170},
        {"fieldname": "employee_pending_usd", "label": _("Pendiente trabajador US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "pending_core_usd", "label": _("Pendiente core US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "employer_receivable_usd", "label": _("Deducido sin remesa asignada US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 225},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 105},
        {"fieldname": "deducted_nio", "label": _("Deducido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 110},
        {"fieldname": "remitted_nio", "label": _("Remitido C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 115},
        {"fieldname": "employee_pending_nio", "label": _("Pendiente trabajador C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 145},
        {"fieldname": "pending_core_nio", "label": _("Pendiente core C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 125},
        {"fieldname": "employer_receivable_nio", "label": _("Deducido sin remesa asignada C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 225},
        {"fieldname": "operational_status", "label": _("Estado operativo"), "fieldtype": "Data", "width": 230},
    ]
