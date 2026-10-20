import frappe
from frappe import _
from frappe.utils import flt

from credinomina_reconciliation.aging import collection_shortfall_usd
from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.reconciliation import AMOUNT_TOLERANCE
from credinomina_reconciliation.rounding import MONEY_EPSILON, decimal_value, money, money_float
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.client_position import load_position, summary


def execute(filters=None):
    filters = frappe._dict(filters or {})
    collection_columns, collections = _collection_report(filters)
    data = load_position(collections, filters)
    message = _("Totales de la selección. Cobranza: control de calidad; aplicaciones y ajustes: saldo financiero. ")
    message += '<details><summary>' + _("Cómo se calcula") + '</summary>' + _("Posición actual de conciliación, no saldo contractual del préstamo. "
        "Cobranza muestra diferencias frente a las aplicaciones (informativas); Aplicación muestra aplicado neto y depósitos asignados; "
        "Partida complementaria incluye las diferencias automáticas por tolerancia y separa distribución, registro contable y gestión de saldos a favor. "
        "La cobranza no genera CxC. La CxC nace de lo aplicado en el core menos depósitos y compensaciones confirmadas. "
        "El aplicado neto ya descuenta los ajustes confirmados vinculados; no se descuentan otra vez. "
        "No reste automáticamente los saldos a favor. "
        "Las fechas filtran el mes de cobranza, la fecha de aplicación o la fecha de la partida, según el tipo; "
        "no reconstruyen saldos históricos. Solo se incluyen documentos visibles para su usuario. "
        "Estado de conciliación expresa la cobertura financiera; Tratamiento identifica si el importe fue documentado, "
        "compensado o requiere aclaración. Registro contable y Gestión externa se consultan por separado.") + '</details>'
    return get_columns(filters), data, message, None, summary(data, filters)


def _collection_report(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    if filters.position_type and filters.position_type != 'Cobranza':
        return columns, []
    periods = get_periods(filters)
    if not periods:
        return columns, []

    period_map = {row.name: row for row in periods}
    row_filters = {"parent": ["in", list(period_map)]}
    if filters.client_number:
        row_filters["client"] = filters.client_number
    if filters.national_id:
        row_filters["national_id"] = filters.national_id
    if filters.loan_number:
        row_filters["loan_number"] = filters.loan_number

    rows = child_records(
        "CN Collection Row", period_map, "CN Reconciliation Period", "collection_rows",
        filters=row_filters,
        fields=[
            "name",
            "parent",
            "client as client_number",
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
            decimal_value(row.expected_nio) / decimal_value(row.expected_usd)
            if money(row.expected_usd) > decimal_value(AMOUNT_TOLERANCE) else 0
        )
        classified_usd = money(row.complementary_usd) + max(money(row.fx_variance_usd), 0)
        rounding = money(row.rounding_adjustment_usd)
        classified_nio = money(classified_usd * decimal_value(rate))
        employee_pending = collection_shortfall_usd(row)
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
                "source_rows": row.get("name"),
                "employee_pending_usd": employee_pending,
                "employee_pending_nio": (
                    money_float(decimal_value(employee_pending) * decimal_value(rate))
                    if employee_pending is not None else None
                ),
                "quality_applied_usd": money_float(row.applied_usd),
                "quality_difference_usd": collection_quality(row)["quality_difference_usd"],
                "operational_status": get_status(row),
            }
        )
        if filters.only_open and _base_status(row).startswith("Conforme"):
            continue
        data.append(item)
    return columns, data


def get_periods(filters):
    conditions = {"reconciliation_mode": "Operativa"}
    if filters.employer:
        conditions["employer"] = filters.employer
    if filters.from_month and filters.to_month:
        conditions["payroll_month"] = ["between", [filters.from_month, filters.to_month]]
    elif filters.from_month:
        conditions["payroll_month"] = [">=", filters.from_month]
    elif filters.to_month:
        conditions["payroll_month"] = ["<=", filters.to_month]
    periods = records(
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
    return collection_quality(row)["quality_status"]


def _base_status(row):
    return get_status(row)


def get_collection_columns():
    return [
        {"fieldname": "payroll_month", "label": _("Mes"), "fieldtype": "Date", "width": 95},
        {"fieldname": "collection_cycle", "label": _("Ciclo"), "fieldtype": "Data", "width": 140},
        {"fieldname": "employer", "label": _("Empresa"), "fieldtype": "Link", "options": "CN Employer", "width": 130},
        {"fieldname": "client_number", "label": _("Nro. Cliente"), "fieldtype": "Data", "width": 110},
        {"fieldname": "client_name", "label": _("Cliente"), "fieldtype": "Data", "width": 220},
        {"fieldname": "national_id", "label": _("Cedula"), "fieldtype": "Data", "width": 135},
        {"fieldname": "loan_number", "label": _("Credito"), "fieldtype": "Data", "width": 100},
        {"fieldname": "installment_number", "label": _("Cuota"), "fieldtype": "Data", "width": 70},
        {"fieldname": "expected_usd", "label": _("Cobranza enviada US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "applied_usd", "label": _("Aplicado al crédito US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "complementary_usd", "label": _("Partida complementaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 155},
        {"fieldname": "remitted_usd", "label": _("Depositado US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 115},
        {"fieldname": "fx_variance_usd", "label": _("Diferencia cambiaria US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 150},
        {"fieldname": "employee_pending_usd", "label": _("Diferencia cobranza/aplicación US$"), "fieldtype": "Currency", "options": "usd_currency", "width": 145},
        {"fieldname": "expected_nio", "label": _("Cobrado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 105},
        {"fieldname": "remitted_nio", "label": _("Depositado C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 115},
        {"fieldname": "employee_pending_nio", "label": _("Diferencia cobranza/aplicación equivalente C$"), "fieldtype": "Currency", "options": "nio_currency", "width": 205},
        {"fieldname": "operational_status", "label": _("Estado operativo"), "fieldtype": "Data", "width": 230},
    ]


def get_columns(filters=None):
    view = (filters or {}).get('position_type')
    columns = [
        {'fieldname': 'event_date', 'label': _('Fecha'), 'fieldtype': 'Date', 'width': 105},
        {'fieldname': 'position_type', 'label': _('Tipo'), 'fieldtype': 'Data', 'width': 175},
        {'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link', 'options': 'CN Employer', 'width': 155},
        {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 220},
        {'fieldname': 'client_number', 'label': _('Nro. Cliente'), 'fieldtype': 'Data', 'width': 110},
        {'fieldname': 'loan_number', 'label': _('Nro. Crédito'), 'fieldtype': 'Data', 'width': 120},
        {'fieldname': 'source_doctype', 'label': _('Tipo de origen'), 'fieldtype': 'Data', 'hidden': 1},
        {'fieldname': 'source_document', 'label': _('Documento de origen'), 'fieldtype': 'Dynamic Link', 'options': 'source_doctype', 'width': 190},
    ]
    metrics = []
    if not view or view == 'Cobranza':
        metrics.extend([('expected_usd', 'Cobranza US$'), ('quality_applied_usd', 'Aplicado para control US$'),
                        ('quality_difference_usd', 'Diferencia de control US$')])
    if not view or view == 'Aplicación':
        metrics.extend([('applied_usd', 'Aplicado neto US$'), ('remitted_usd', 'Depósito asignado US$'),
                        ('applied_pending_usd', 'Aplicado pendiente US$')])
    if not view or view == 'Partida complementaria':
        metrics.extend([('complementary_usd', 'Complementaria original US$'), ('complementary_used_usd', 'Utilizado US$'),
                        ('complementary_pending_usd', 'Complementaria pendiente US$'), ('credit_resolved_usd', 'Gestionado US$'),
                        ('credit_pending_usd', 'Saldo a favor por gestionar US$')])
    if not view or view == 'CxC por ajuste':
        metrics.extend([('company_receivable_usd', 'CxC a la empresa por ajuste US$'),
                        ('employee_receivable_usd', 'CxC al empleado por ajuste US$')])
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Currency', 'options': 'usd_currency', 'width': 150}
                   for field, label in metrics)
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
                   for field, label, width in [('category', 'Categoría', 180), ('operational_status', 'Estado de conciliación', 210),
                       ('subcategory', 'Subcategoría', 175), ('reason_type', 'Motivo', 175), ('difference_reason', 'Razón de la diferencia', 190), ('treatment_status', 'Tratamiento', 200),
                       ('accounting_status', 'Registro contable', 170), ('management_status', 'Gestión externa', 170),
                       ('observation', 'Observación', 350), ('source_rows', 'Filas de origen', 170)])
    if not (filters or {}).get('detailed_columns'):
        secondary = {'client_number', 'observation', 'source_rows', 'subcategory', 'reason_type', 'treatment_status'}
        if view:
            secondary.add('position_type')
        if view == 'Partida complementaria':
            secondary.update({'loan_number', 'complementary_usd', 'complementary_used_usd', 'credit_resolved_usd'})
        if view != 'Partida complementaria':
            secondary.update({'category', 'accounting_status', 'management_status'})
        for column in columns:
            if column['fieldname'] in secondary:
                column['hidden'] = 1
    if view == 'Partida complementaria':
        # Put classification and the two remaining obligations before audit data.
        leading = ('event_date', 'employer', 'category', 'complementary_pending_usd', 'credit_pending_usd',
                   'operational_status', 'client_name', 'source_document', 'accounting_status', 'management_status')
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    return columns
