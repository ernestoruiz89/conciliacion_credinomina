"""Current operational balances aged at a chosen date, not a historical snapshot."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from credinomina_reconciliation.aging import age_balance, operational_balances


def execute(filters=None):
    filters = frappe._dict(filters or {})
    as_of = getdate(filters.get("as_of_date") or nowdate())
    from_month = getdate(filters.from_month).replace(day=1) if filters.get("from_month") else None
    to_month = getdate(filters.to_month).replace(day=1) if filters.get("to_month") else None
    if from_month and to_month and from_month > to_month:
        frappe.throw(_("El mes inicial no puede ser posterior al mes final."))
    period_filters = {
        "reconciliation_mode": "Operativa",
    }
    if from_month:
        period_filters["payroll_month"] = ["between", [from_month, min(to_month or as_of, as_of)]]
    else:
        period_filters["payroll_month"] = ["<=", min(to_month or as_of, as_of)]
    if filters.get("employer"):
        period_filters["employer"] = filters.employer
    periods = frappe.get_list(
        "CN Reconciliation Period", filters=period_filters,
        fields=[
            "name", "employer", "payroll_month", "collection_cycle",
            "cutoff_date", "remittance_due_date", "status",
        ],
        order_by="employer asc, payroll_month asc",
        limit_page_length=10000,
    )
    if not periods:
        return get_columns(), []
    period_by_name = {period.name: period for period in periods}
    row_filters = {"parent": ["in", list(period_by_name)]}
    for field in ("client_number", "national_id", "loan_number"):
        if filters.get(field):
            row_filters[field] = filters[field]
    collection_rows = frappe.get_all(
        "CN Collection Row", filters=row_filters,
        fields=[
            "name", "parent", "client", "client_number", "client_name",
            "national_id", "loan_number", "installment_number",
            "expected_usd", "deducted_usd", "applied_usd", "remitted_usd",
            "fx_variance_usd", "rounding_adjustment_usd", "deduction_status",
        ],
        order_by="parent asc, idx asc", limit_page_length=100000,
    )
    data = []
    summary = defaultdict(float)
    for row in collection_rows:
        period = period_by_name[row.parent]
        for balance in operational_balances(row, period):
            balance_type = balance["balance_type"]
            if filters.get("balance_type") and filters.balance_type != balance_type:
                continue
            aged = age_balance(balance["amount_usd"], balance["due_date"], as_of)
            item = {
                "period": period.name,
                "usd_currency": "USD",
                "payroll_month": period.payroll_month,
                "collection_cycle": period.collection_cycle or "Mensual",
                "employer": period.employer,
                "client": row.client,
                "client_number": row.client_number,
                "client_name": row.client_name,
                "national_id": row.national_id,
                "loan_number": row.loan_number,
                "installment_number": row.installment_number,
                "balance_type": balance_type,
                "due_date": balance["due_date"],
                "age_days": aged["age_days"],
                "amount_usd": balance["amount_usd"],
                "provision_review_usd": balance["provision_review_usd"],
                "not_due": aged["not_due"],
                "days_1_30": aged["days_1_30"],
                "days_31_60": aged["days_31_60"],
                "days_61_90": aged["days_61_90"],
                "days_over_90": aged["days_over_90"],
                "without_date": aged["without_date"],
            }
            data.append(item)
            summary[balance_type] += flt(balance["amount_usd"])
    data.sort(key=lambda row: (
        row["employer"], row["client_name"], str(row["payroll_month"]),
        row["balance_type"], row["loan_number"],
    ))
    report_summary = [
        {
            "label": _(label), "value": round(summary[label], 4),
            "indicator": indicator, "datatype": "Currency", "currency": "USD",
        }
        for label, indicator in (
            ("Cuota no deducida al trabajador", "red"),
            ("Deducido sin remesa asignada", "orange"),
            ("Detalle de empresa pendiente", "blue"),
        )
        if summary[label] > 0
    ]
    message = _(
        "Antigüedad de saldos operativos actuales según la fecha indicada. "
        "Los tipos de saldo son distintos y no deben sumarse como una sola deuda. "
        "Una deducción sin remesa asignada puede estar cubierta por un depósito recibido sin detalle; "
        "por sí sola no prueba una cuenta por cobrar a la empresa. "
        "La cuota no deducida requiere cotejo con el saldo y la mora del core antes de calcular provisiones; "
        "este reporte no registra un asiento ni reconstruye saldos históricos a una fecha anterior."
    )
    return get_columns(), data, message, None, report_summary


def get_columns():
    return [
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 190},
        {"fieldname": "client_name", "label": _("Cliente"), "fieldtype": "Data", "width": 210},
        {"fieldname": "client_number", "label": _("Nro. Cliente"), "fieldtype": "Data", "width": 105},
        {"fieldname": "national_id", "label": _("Cédula"), "fieldtype": "Data", "width": 120},
        {"fieldname": "loan_number", "label": _("Nro. Crédito"), "fieldtype": "Data", "width": 105},
        {"fieldname": "period", "label": _("Período"), "fieldtype": "Link", "options": "CN Reconciliation Period", "width": 160},
        {"fieldname": "payroll_month", "label": _("Mes de cobranza"), "fieldtype": "Date", "width": 110},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 120},
        {"fieldname": "installment_number", "label": _("Cuota"), "fieldtype": "Data", "width": 65},
        {"fieldname": "balance_type", "label": _("Tipo de saldo"), "fieldtype": "Data", "width": 220},
        {"fieldname": "due_date", "label": _("Fecha de referencia"), "fieldtype": "Date", "width": 120},
        {"fieldname": "age_days", "label": _("Días transcurridos"), "fieldtype": "Int", "width": 110},
        {"fieldname": "amount_usd", "label": _("Saldo US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 110},
        {"fieldname": "not_due", "label": _("No vencido US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_1_30", "label": _("1–30 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 120},
        {"fieldname": "days_31_60", "label": _("31–60 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_61_90", "label": _("61–90 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_over_90", "label": _("Más de 90 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "without_date", "label": _("Sin fecha US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 120},
        {"fieldname": "provision_review_usd", "label": _("Cuota para revisar en core US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 180},
    ]
