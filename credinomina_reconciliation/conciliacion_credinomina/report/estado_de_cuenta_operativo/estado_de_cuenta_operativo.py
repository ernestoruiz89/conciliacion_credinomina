import frappe
from frappe import _
from frappe.utils import cint, getdate

from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.rounding import decimal_value, money, money_float
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.client_position import (
    FINANCIAL_VIEW, load_position, normalize_position_type, selected_position_types, summarize_selection, net_balance_summary,
)


def execute(filters=None):
    filters = frappe._dict(filters or {})
    filters.position_type = normalize_position_type(filters.get('position_type', FINANCIAL_VIEW))
    filters.detailed_columns = cint(filters.get('detailed_columns'))
    snapshot = None
    if filters.get('cutoff_date'):
        from credinomina_reconciliation.operational_statement_cutoff import load_snapshot, build
        snapshot = load_snapshot(filters)
        columns, collections = _collection_report(filters, snapshot['position_context']['collections'])
        data = build(collections, snapshot, filters)
    else:
        columns, collections = _collection_report(filters)
        data = load_position(collections, filters)
    # Frappe can also restrict a report row by a linked company, for example
    # on the beneficiary of a readable deposit. Total only rows it will show.
    if data and frappe.session.user and frappe.session.user != 'Administrator':
        from frappe.desk.query_report import get_filtered_data
        data = get_filtered_data('CN Reconciliation Period', columns, data, frappe.session.user)
    message = _("Totales de la selección. Cada columna suma los movimientos incluidos por los filtros. El guion indica un importe sin determinar o que no aplica. ")
    if snapshot:
        from credinomina_reconciliation.historical_cutoff import cutoff_message
        message += cutoff_message(snapshot) + '<br>'
    position_message = (_("Posición de conciliación al corte seleccionado, no saldo contractual del préstamo. ") if snapshot
                        else _("Posición actual de conciliación, no saldo contractual del préstamo. "))
    date_message = (_("Desde y Hasta limitan las fechas de origen; los saldos corresponden al corte histórico. ") if snapshot
                    else _("Desde y Hasta filtran por el mes de cobranza o por la fecha de la aplicación, depósito o partida. Los saldos son actuales. "))
    message += '<details><summary>' + _("Cómo se calcula") + '</summary>' + position_message + _(
        "Cobranza muestra diferencias frente a las aplicaciones (informativas); Aplicación muestra aplicado neto y depósitos asignados; "
        "Partida complementaria incluye las diferencias automáticas por tolerancia y separa distribución, registro contable y gestión de saldos a favor. "
        "La CxC por aplicaciones parte del aplicado en el core y descuenta su cobertura confirmada. "
        "Depósito asignado muestra el efectivo bancario. Los saldos a favor aplicados y las complementarias que cubren una aplicación se explican en Observación. "
        "El aplicado neto ya descuenta los ajustes confirmados vinculados; no se descuentan otra vez. "
        "Saldo neto de conciliación = aplicaciones pendientes − depósitos pendientes + complementarias pendientes con signo contable (débito positivo, crédito negativo). "
        "Se calcula sobre la selección visible; si falta un saldo o el signo contable, se indica como no determinable. "
        "No reste automáticamente los saldos a favor. "
        "La fila Depósito con saldo pendiente corresponde al remanente pendiente, incluso cuando el depósito ya tiene aplicaciones. "
        "Depósito con saldo pendiente muestra efectivo confirmado pendiente de distribuir, separado de saldos a favor ya documentados; "
        "solo atribuye importes al cliente cuando el detalle cabe en el efectivo pendiente. No se compensa automáticamente con aplicaciones. "
    ) + date_message + _(
        "Solo se incluyen documentos visibles para su usuario. "
        "Estado de conciliación expresa la cobertura financiera; Tratamiento identifica si el importe fue documentado, "
        "compensado o requiere aclaración. Registro contable y Gestión externa se consultan por separado.") + '</details>'
    totals, amounts = summarize_selection(data, filters)
    totals.extend(net_balance_summary(data, amounts, filters))
    for row in data:
        row.pop('_shared_bank_cash', None)
        row.pop('_bank_basis', None)
    if data:
        monetary = {col['fieldname']: amounts.get(col['fieldname']) for col in columns if col['fieldtype'] == 'Currency'}
        data.append(dict(monetary, client_name=_("Total"), position_type='', source_document=None,
                         is_total_row=1, usd_currency='USD'))
    return columns, data, message, None, totals


def _collection_report(filters=None, projected_collections=None):
    filters = frappe._dict(filters or {})
    columns = get_columns(filters)
    if filters.position_type and filters.position_type != 'Cobranza':
        return columns, []
    periods = get_periods(filters)
    if not periods:
        return columns, []

    period_map = {row.name: row for row in periods}
    # Narrow the read without losing padded identifiers or a legacy loan with
    # the implicit first cycle. The statement compares the complete keys.
    row_filters = {}
    if filters.client_number:
        row_filters["client"] = ["like", "%" + canonical_identifier(filters.client_number) + "%"]
    if filters.national_id:
        row_filters["national_id"] = ["like", "%" + canonical_identifier(filters.national_id) + "%"]
    if filters.loan_number:
        row_filters["loan_number"] = ["like", "%" + canonical_credit_number(filters.loan_number).split("-")[0] + "%"]

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
            "difference_reason", "difference_notes",
        ],
        order_by="parent asc, idx asc",
    )
    data = []
    for row in rows:
        period = period_map[row.parent]
        if projected_collections is not None:
            if row.name not in projected_collections:
                frappe.throw(_("No está disponible la cobranza necesaria para reconstruir el corte."))
            row = frappe._dict(row, applied_usd=projected_collections[row.name]['applied_usd'])
        quality = collection_quality(row)
        if cint(filters.only_open) and quality["quality_status"].startswith("Conforme"):
            continue
        difference = quality["quality_difference_usd"]
        employee_pending = abs(difference) if difference is not None else None
        rate = (decimal_value(row.expected_nio) / decimal_value(row.expected_usd)
                if money(row.expected_usd) > 0 and money(row.expected_nio) > 0 else None)
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
                "expected_usd": quality["quality_expected_usd"],
                "source_rows": row.get("name"),
                "employee_pending_usd": employee_pending,
                "employee_pending_nio": (
                    money_float(decimal_value(employee_pending) * decimal_value(rate))
                    if employee_pending is not None and rate is not None else None
                ),
                "quality_applied_usd": money_float(row.applied_usd),
                "quality_difference_usd": difference,
                "operational_status": quality["quality_status"],
                "difference_reason": row.get('difference_reason'),
                "difference_notes": row.get('difference_notes'),
            }
        )
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
        if not filters.get('cutoff_date') or getdate(period.payroll_month) <= getdate(filters.cutoff_date)
    ]


def get_status(row):
    return collection_quality(row)["quality_status"]


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
    view = normalize_position_type((filters or {}).get('position_type', FINANCIAL_VIEW))
    kinds = selected_position_types(view)
    columns = [
        {'fieldname': 'event_date', 'label': _('Fecha'), 'fieldtype': 'Date', 'width': 105},
        {'fieldname': 'position_type', 'label': _('Tipo'), 'fieldtype': 'Data', 'width': 230},
        {'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link', 'options': 'CN Employer', 'width': 155},
        {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 220},
        {'fieldname': 'client_number', 'label': _('Nro. Cliente'), 'fieldtype': 'Data', 'width': 110},
        {'fieldname': 'loan_number', 'label': _('Nro. Crédito'), 'fieldtype': 'Data', 'width': 120},
        {'fieldname': 'source_doctype', 'label': _('Tipo de origen'), 'fieldtype': 'Data', 'hidden': 1},
        {'fieldname': 'source_document', 'label': _('Documento de origen'), 'fieldtype': 'Dynamic Link', 'options': 'source_doctype', 'width': 190},
    ]
    metrics = []
    if kinds is None or 'Cobranza' in kinds:
        metrics.extend([('expected_usd', 'Cobranza US$'), ('quality_applied_usd', 'Aplicado para control US$'),
                        ('quality_difference_usd', 'Diferencia de control US$')])
    if kinds is None or 'Aplicación' in kinds:
        metrics.extend([('applied_usd', 'Aplicado neto US$'), ('remitted_usd', 'Depósito asignado US$'),
                        ('applied_pending_usd', 'Aplicado pendiente US$')])
    if kinds is None or 'Depósito con saldo pendiente' in kinds:
        metrics.append(('deposit_pending_usd', 'Saldo pendiente del depósito US$'))
        metrics.append(('deposit_detail_pending_usd', 'Pendiente según detalle US$'))
    if kinds is None or 'Partida complementaria' in kinds:
        metrics.extend([('complementary_usd', 'Complementaria original US$'), ('complementary_used_usd', 'Utilizado US$'),
                        ('complementary_pending_usd', 'Complementaria pendiente US$'), ('credit_resolved_usd', 'Gestionado US$'),
                        ('credit_pending_usd', 'Saldo a favor por gestionar US$')])
    if kinds is None or 'CxC por ajuste' in kinds:
        metrics.extend([('company_receivable_usd', 'CxC a la empresa por ajuste US$'),
                        ('employee_receivable_usd', 'CxC al empleado por ajuste US$')])
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Currency', 'options': 'usd_currency', 'width': 150}
                   for field, label in metrics)
    columns.extend({'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
                   for field, label, width in [('category', 'Categoría', 180), ('operational_status', 'Estado de conciliación', 210),
                       ('subcategory', 'Subcategoría', 175), ('reason_type', 'Motivo', 175), ('difference_reason', 'Razón de la diferencia', 190),
                       ('difference_notes', 'Comentarios de la diferencia', 300), ('treatment_status', 'Tratamiento', 200),
                       ('accounting_status', 'Registro contable', 170), ('management_status', 'Gestión externa', 170),
                       ('observation', 'Observación', 350), ('source_rows', 'Filas de origen', 170)])
    if not cint((filters or {}).get('detailed_columns')):
        primary = {'event_date', 'employer', 'client_name', 'loan_number', 'source_document', 'operational_status',
                   'difference_reason', 'difference_notes',
                   'applied_usd', 'remitted_usd', 'applied_pending_usd', 'deposit_pending_usd', 'complementary_pending_usd'}
        if not view or view == FINANCIAL_VIEW:
            primary.add('position_type')
        if not view or view == 'Cobranza':
            primary.update({'expected_usd', 'quality_applied_usd', 'quality_difference_usd'})
        if not view or view == 'CxC por ajuste':
            primary.update({'company_receivable_usd', 'employee_receivable_usd'})
        for column in columns:
            if column['fieldname'] not in primary:
                column['hidden'] = 1
    if view == 'Partida complementaria':
        # Put classification and the two remaining obligations before audit data.
        leading = ('event_date', 'employer', 'category', 'complementary_pending_usd', 'credit_pending_usd',
                   'operational_status', 'client_name', 'source_document', 'accounting_status', 'management_status')
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    return columns
