"""Current applied/unpaid balances in both modes, not a historical snapshot."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from credinomina_reconciliation.aging import age_balance, operational_balances
from credinomina_reconciliation.application_aging import (
    APPLICATION_BALANCE, TOTAL_RECEIVABLE_BALANCE, ADJUSTMENT_BALANCE, application_balances,
)
from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.rounding import money_float, sum_money
from credinomina_reconciliation.report_records import records, child_records


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if filters.get("balance_type") in (None, "", TOTAL_RECEIVABLE_BALANCE, APPLICATION_BALANCE, ADJUSTMENT_BALANCE):
        return _execute_applications(filters)
    if frappe.utils.cint(filters.get('historical_cutoff')):
        frappe.throw(_('El corte histórico aplica a CxC total, aplicaciones pendientes o CxC por ajustes; no a controles de cobranza.'))
    return _execute_operational(filters)


def _execute_applications(filters):
    as_of = getdate(filters.get("as_of_date") or nowdate())
    from_month = getdate(filters.from_month).replace(day=1) if filters.get("from_month") else None
    to_month = getdate(filters.to_month).replace(day=1) if filters.get("to_month") else None
    if from_month and to_month and from_month > to_month:
        frappe.throw(_("El mes inicial no puede ser posterior al mes final."))
    include_applications = filters.get('balance_type') != ADJUSTMENT_BALANCE
    include_adjustments = filters.get('balance_type') != APPLICATION_BALANCE
    cutoff = None
    if frappe.utils.cint(filters.get('historical_cutoff')):
        from credinomina_reconciliation.historical_cutoff import load_cutoff
        cutoff = load_cutoff(dict(cutoff_date=str(as_of), employer=filters.get('employer')))
        periods = cutoff['periods']
        applications = [row for row in cutoff['applications'] if row.get('amount_usd') is None or row['amount_usd'] > 0]
    else:
        sources, imports, periods, collections, employers = load_application_context(
            employer=filters.get('employer'), include_applications=include_applications)
        applications = application_balances(sources, imports, periods, collections, employers, as_of) if include_applications else []
    data = []
    for row in applications if include_applications else ():
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
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    receivables = (cutoff['receivables'] if cutoff else load_receivables(employer=filters.get('employer'))) if include_adjustments else []
    for item in receivables:
        if filters.get('employer') and item.get('employer') != filters['employer']:
            continue
        if any(filters.get(field) and item.get(field) != filters[field]
               for field in ('client_number', 'national_id', 'loan_number')):
            continue
        when = getdate(item['posting_date']).replace(day=1)
        if when > as_of or (from_month and when < from_month) or (to_month and when > to_month):
            continue
        period = periods.get(item.get('period'), {})
        if filters.get('reconciliation_mode') and period.get('reconciliation_mode') != filters['reconciliation_mode']:
            continue
        due = item.get('credit_commitment_date')
        data.append(dict(item, balance_type=ADJUSTMENT_BALANCE, amount_usd=item['receivable_usd'],
            complementary_item=item['name'], payroll_month=period.get('payroll_month') or item['posting_date'],
            due_date=due, usd_currency='USD', **age_balance(item['receivable_usd'], due, as_of),
            payment_term_origin='Fecha compromiso del ajuste' if due else 'Sin fecha compromiso',
            observation='CxC trasladada a la empresa; el registro contable no liquida el cobro.'))
    data.sort(key=lambda row: (row.get("employer") or "", str(row.get("due_date") or "9999"),
                              row.get("client_name") or "", row.get("loan_number") or ""))
    total_label = ('CxC pendiente (aplicaciones y ajustes)' if include_applications and include_adjustments
                   else 'Aplicaciones pendientes' if include_applications else ADJUSTMENT_BALANCE)
    summary = [
        {"label": _(label), "value": money_float(sum_money(row.get(field) for row in data)),
         "indicator": indicator, "datatype": "Currency", "currency": "USD"}
        for label, field, indicator in (
            (total_label, "amount_usd", "orange"),
            ("No vencido", "not_due", "blue"),
            ("Sin fecha / distribución pendiente", "without_date", "red"),
        )
    ]
    missing_fx = sum("amount_usd" not in row for row in data)
    if missing_fx:
        summary.append({"label": _("Aplicaciones sin conversión US$"), "value": missing_fx,
                        "indicator": "red", "datatype": "Int"})
    help_text = _(
        "CxC actual: aplicaciones pendientes de cubrir y CxC por ajustes de depósito, en US$. "
        "Los ajustes usan su fecha compromiso; si falta, se muestran sin vencimiento. "
        "Solo depósitos o compensaciones vinculados liquidan la CxC del ajuste. "
        "El aplicado neto ya descuenta las compensaciones confirmadas vinculadas a la aplicación; no se restan dos veces. "
        "La cobranza solicitada y las deducciones son controles de la primera conciliación, no crean esta CxC. "
        "Incluye histórico y operativo; no depende de haber recibido el detalle de deducción. "
        "El vencimiento se calcula con grace_days de la empresa desde el primer día del mes siguiente "
        "a la fecha de aplicación: 10 significa el día 10; la mora empieza el día 11. "
        "No se descuentan depósitos sin asignar ni diferencias cambiarias en revisión. "
        "Si una cuota agrupa vencimientos distintos y un pago parcial no identifica qué aplicación cubre, "
        "su saldo queda sin distribución de antigüedad. "
        "Se conserva el vencimiento al cargar; cambiar el plazo de la empresa no modifica lo ya cargado. "
        "Origen del plazo identifica las estimaciones de datos migrados, que no acreditan el convenio histórico. "
        "La fecha elegida mide la antigüedad del saldo actual, "
        "no reconstruye saldos pasados. Las cuotas no deducidas se consultan por separado en Tipo de saldo. "
        "Los filtros de mes usan el mes de cobranza de la aplicación (sin período, su fecha de aplicación) "
        "y la fecha de partida de la CxC por ajuste, mostrada en Fecha de ajuste."
    )
    message = (_("Saldo actual, no corte histórico. CxC total reúne aplicaciones pendientes y CxC por ajustes; "
                 "puede consultar cada tipo por separado. Los depósitos sin asignar y los saldos a favor "
                 "no se compensan automáticamente.") +
               '<details><summary>' + _("Cómo se calculan los saldos y la antigüedad") +
               '</summary><p>' + help_text + '</p></details>')
    columns = get_application_columns()
    if cutoff:
        from credinomina_reconciliation.historical_cutoff import cutoff_message
        message = cutoff_message(cutoff) + _(' La antigüedad usa el vencimiento conservado en cada aplicación; los plazos no conservados se estiman con la configuración vigente.')
        data = [dict(row, cutoff_date=cutoff['cutoff_date'], cutoff_warning='; '.join(cutoff['warnings'])) for row in data]
        columns += [
            {'fieldname': 'cutoff_date', 'label': _('Fecha de corte histórico'), 'fieldtype': 'Date', 'width': 150},
            {'fieldname': 'cutoff_warning', 'label': _('Advertencias del corte'), 'fieldtype': 'Data', 'width': 350},
        ]
    return columns, data, message, None, summary


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
    periods = list(records(
        "CN Reconciliation Period", filters=period_filters,
        fields=[
            "name", "employer", "payroll_month", "collection_cycle",
            "cutoff_date", "remittance_due_date", "status",
        ],
        order_by="employer asc, payroll_month asc",
    ))
    if not periods:
        return get_columns(), []
    period_by_name = {period.name: period for period in periods}
    row_filters = {}
    for field in ("client_number", "national_id", "loan_number"):
        if filters.get(field):
            row_filters[field] = filters[field]
    collection_rows = child_records(
        "CN Collection Row", period_by_name, "CN Reconciliation Period", "collection_rows", filters=row_filters,
        fields=[
            "name", "parent", "client", "client_number", "client_name",
            "national_id", "loan_number", "installment_number",
            "expected_usd", "deducted_usd", "applied_usd", "remitted_usd",
            "fx_variance_usd", "rounding_adjustment_usd", "deduction_status",
        ],
        order_by="parent asc, idx asc",
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
                "days_1_15": aged["days_1_15"],
                "days_16_30": aged["days_16_30"],
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
            ("Cobranza no deducida (informativo)", "red"),
            ("Deducido sin depósito asignado", "orange"),
            ("Detalle de empresa pendiente", "blue"),
            ("Detalle de empresa por aclarar", "orange"),
        )
        if summary[label] > 0
    ]
    message = _(
        "Seguimiento informativo de diferencias de cobranza según la fecha indicada. "
        "Estos importes no son cuentas por cobrar: la CxC se consulta en Aplicado pendiente de depósito. "
        "Una deducción sin depósito asignado puede estar cubierta por un depósito recibido sin detalle; "
        "por sí sola no prueba una cuenta por cobrar a la empresa. "
        "Este control no calcula deuda del empleado, provisiones ni saldos históricos a una fecha anterior."
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
        {"fieldname": "days_1_15", "label": _("1–15 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 120},
        {"fieldname": "days_16_30", "label": _("16–30 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_31_60", "label": _("31–60 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_61_90", "label": _("61–90 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 125},
        {"fieldname": "days_over_90", "label": _("Más de 90 días US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "without_date", "label": _("Sin fecha US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 120},
    ]


def get_application_columns():
    columns = [column for column in get_columns() if column["fieldname"] not in {
        "provision_review_usd", "due_date", "age_days",
    }]
    index = next(i for i, column in enumerate(columns) if column["fieldname"] == "amount_usd")
    columns[index:index] = [
        {"fieldname": "reconciliation_mode", "label": _("Modalidad"), "fieldtype": "Data", "width": 105},
        {"fieldname": "source_import", "label": _("Importación"), "fieldtype": "Link", "options": "CN Accounting Import", "width": 170},
        {"fieldname": "application_date", "label": _("Fecha aplicación (primera si agrupada)"), "fieldtype": "Date", "width": 160},
        {"fieldname": "due_date", "label": _("Vencimiento de pago"), "fieldtype": "Date", "width": 145},
        {"fieldname": "age_days", "label": _("Días de atraso"), "fieldtype": "Int", "width": 110},
        *[{"fieldname": field, "label": _(label), "fieldtype": "Currency", "options": "usd_currency", "width": 145}
          for field, label in (("applied_usd", "Aplicado US$"), ("paid_usd", "Depósito asignado US$"),
                               ("adjustment_usd", "Ajuste conciliación US$"), ("fx_variance_usd", "Diferencia cambiaria US$"))],
    ]
    columns.append({"fieldname": "observation", "label": _("Observación"), "fieldtype": "Data", "width": 360})
    columns.append({"fieldname": "complementary_item", "label": _("Partida complementaria"), "fieldtype": "Link", "options": "CN Complementary Item", "width": 180})
    columns.append({"fieldname": "posting_date", "label": _("Fecha de ajuste"), "fieldtype": "Date", "width": 130})
    columns.append({"fieldname": "payment_term_origin", "label": _("Origen del plazo"), "fieldtype": "Data", "width": 300})
    return columns
