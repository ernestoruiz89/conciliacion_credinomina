"""Current applied/unpaid balances in both modes, not a historical snapshot."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from credinomina_reconciliation.aging import age_balance, operational_balances
from credinomina_reconciliation.application_aging import APPLICATION_BALANCE, application_balances
from credinomina_reconciliation.rounding import money_float, sum_money


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("balance_type") or filters.balance_type == APPLICATION_BALANCE:
        return _execute_applications(filters)
    return _execute_operational(filters)


def _execute_applications(filters):
    as_of = getdate(filters.get("as_of_date") or nowdate())
    from_month = getdate(filters.from_month).replace(day=1) if filters.get("from_month") else None
    to_month = getdate(filters.to_month).replace(day=1) if filters.get("to_month") else None
    if from_month and to_month and from_month > to_month:
        frappe.throw(_("El mes inicial no puede ser posterior al mes final."))
    # First resolve readable parents. Child table queries must never bypass
    # the period/import permissions by selecting all child rows directly.
    periods = {row.name: row for row in frappe.get_list(
        "CN Reconciliation Period",
        fields=["name", "employer", "payroll_month", "collection_cycle", "reconciliation_mode"],
        limit_page_length=0,
    )}
    imports = {row.name: row for row in frappe.get_list(
        "CN Source Import",
        filters={"status": ["in", ["Importado", "Importado con excepciones"]],
                 "source_type": "Movimientos contables"},
        fields=["name", "employer", "historical_backfill", "historical_period"],
        limit_page_length=0,
    )}
    sources = frappe.get_all(
        "CN Source Row", filters={"parent": ["in", list(imports)],
                                  "parenttype": "CN Source Import", "parentfield": "rows",
                                  "event_type": "Aplicacion", "effective": 1},
        fields=[
            "name", "parent", "event_type", "event_date", "effective", "match_status",
            "client", "client_name", "client_number", "national_id", "loan_number", "installment_number",
            "currency", "amount", "equivalent_currency", "equivalent_amount", "fx_basis", "manual_fx_rate",
            "processing_route", "historical_period", "portfolio_employer", "collection_row_id",
            "application_allocation_detail", "historical_remitted_usd", "historical_detail",
        ], limit_page_length=0,
    ) if imports else []
    collections = {row.name: row for row in frappe.get_all(
        "CN Collection Row", filters={"parent": ["in", list(periods)],
                                      "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows"},
        fields=["name", "parent", "client", "client_name", "client_number", "national_id", "loan_number",
                "installment_number", "remittance_detail", "rounding_adjustment_usd", "fx_variance_usd"],
        limit_page_length=0,
    )} if periods else {}
    employer_names = {row.employer for row in list(periods.values()) + list(imports.values()) if row.employer}
    employer_names.update(row.portfolio_employer for row in sources if row.portfolio_employer)
    employers = {row.name: row for row in frappe.get_all(
        "CN Employer", filters={"name": ["in", list(employer_names)]},
        fields=["name", "grace_days"], limit_page_length=0,
    )} if employer_names else {}
    data = []
    for row in application_balances(sources, imports, periods, collections, employers, as_of):
        if any(filters.get(field) and row.get(field) != filters[field]
               for field in ("employer", "reconciliation_mode", "client_number", "national_id", "loan_number")):
            continue
        # Unlinked applications remain visible, using their application month.
        month = row.get("payroll_month") or row.get("application_date")
        if month:
            month = getdate(month).replace(day=1)
            if month > as_of or (from_month and month < from_month) or (to_month and month > to_month):
                continue
        elif from_month or to_month:
            continue
        data.append(row)
    data.sort(key=lambda row: (row.get("employer") or "", str(row.get("due_date") or "9999"),
                              row.get("client_name") or "", row.get("loan_number") or ""))
    summary = [
        {"label": _(label), "value": money_float(sum_money(row.get(field) for row in data)),
         "indicator": indicator, "datatype": "Currency", "currency": "USD"}
        for label, field, indicator in (
            ("Aplicado pendiente de depósito", "amount_usd", "orange"),
            ("No vencido", "not_due", "blue"),
            ("Sin fecha / distribución pendiente", "without_date", "red"),
        )
    ]
    missing_fx = sum("amount_usd" not in row for row in data)
    if missing_fx:
        summary.append({"label": _("Aplicaciones sin conversión US$"), "value": missing_fx,
                        "indicator": "red", "datatype": "Int"})
    message = _(
        "Saldo actual = aplicado + ajuste de conciliación − depósito asignado al crédito, en US$. "
        "Incluye histórico y operativo; no depende de haber recibido el detalle de deducción. "
        "El vencimiento se calcula con grace_days de la empresa desde el primer día del mes siguiente "
        "a la fecha de aplicación: 10 significa el día 10; la mora empieza el día 11. "
        "No se descuentan depósitos sin asignar ni diferencias cambiarias en revisión. "
        "Si una cuota agrupa vencimientos distintos y un pago parcial no identifica qué aplicación cubre, "
        "su saldo queda sin distribución de antigüedad. "
        "Se usa el plazo vigente de la empresa. La fecha elegida mide la antigüedad del saldo actual, "
        "no reconstruye saldos pasados. Las cuotas no deducidas se consultan por separado en Tipo de saldo."
    )
    return get_application_columns(), data, message, None, summary


def _execute_operational(filters):
    filters = frappe._dict(filters or {})
    if filters.get("reconciliation_mode") == "Historica":
        return get_columns(), []
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
            "label": _(label), "value": money_float(summary[label]),
            "indicator": indicator, "datatype": "Currency", "currency": "USD",
        }
        for label, indicator in (
            ("CxC a empleados (cuota no deducida)", "red"),
            ("Deducido sin depósito asignado", "orange"),
            ("Detalle de empresa pendiente", "blue"),
        )
        if summary[label] > 0
    ]
    message = _(
        "Antigüedad de saldos operativos actuales según la fecha indicada. "
        "Los tipos de saldo son distintos y no deben sumarse como una sola deuda. "
        "Una deducción sin depósito asignado puede estar cubierta por un depósito recibido sin detalle; "
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


def get_application_columns():
    columns = [column for column in get_columns() if column["fieldname"] not in {
        "provision_review_usd", "balance_type", "due_date", "age_days",
    }]
    index = next(i for i, column in enumerate(columns) if column["fieldname"] == "amount_usd")
    columns[index:index] = [
        {"fieldname": "reconciliation_mode", "label": _("Modalidad"), "fieldtype": "Data", "width": 105},
        {"fieldname": "source_import", "label": _("Importación"), "fieldtype": "Link", "options": "CN Source Import", "width": 170},
        {"fieldname": "application_date", "label": _("Fecha aplicación (primera si agrupada)"), "fieldtype": "Date", "width": 160},
        {"fieldname": "due_date", "label": _("Vencimiento de pago"), "fieldtype": "Date", "width": 145},
        {"fieldname": "age_days", "label": _("Días de atraso"), "fieldtype": "Int", "width": 110},
        *[{"fieldname": field, "label": _(label), "fieldtype": "Currency", "options": "usd_currency", "width": 145}
          for field, label in (("applied_usd", "Aplicado US$"), ("paid_usd", "Depósito asignado US$"),
                               ("adjustment_usd", "Ajuste conciliación US$"), ("fx_variance_usd", "Diferencia cambiaria US$"))],
    ]
    columns.append({"fieldname": "observation", "label": _("Observación"), "fieldtype": "Data", "width": 360})
    return columns
