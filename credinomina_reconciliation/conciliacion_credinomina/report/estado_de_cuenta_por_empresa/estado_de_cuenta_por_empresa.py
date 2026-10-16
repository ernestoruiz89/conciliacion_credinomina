from frappe import _
from credinomina_reconciliation.rounding import money, money_float

from credinomina_reconciliation.company_statement import load_detail
from credinomina_reconciliation.company_accounting_statement import (
    CORE, MANAGEMENT, CORE_METRICS, MANAGEMENT_METRICS, CORE_FIELDS, MANAGEMENT_FIELDS,
    load_core_detail, management_detail, matches, selected_scope, summarize, validate_dates,
)


def execute(filters=None):
    filters = filters or {}
    validate_dates(filters)
    scope = {key: value for key, value in filters.items() if key != 'status'}
    section = selected_scope(filters)
    detail, cutoff = [], None
    if section != MANAGEMENT:
        detail.extend(load_core_detail(scope))
    if filters.get('cutoff_date') and section != CORE:
        from credinomina_reconciliation.historical_cutoff import load_cutoff, cutoff_message
        cutoff = load_cutoff(scope)
        detail.extend(management_detail(cutoff['detail']))
    elif section != CORE:
        detail.extend(management_detail(load_detail(scope)))
    data = _presentation(detail, filters)
    message = _('Saldo contable core = débitos originales − créditos originales. '
        'Los saldos a favor y las CxC por gestionar se muestran por separado y no se suman ni se restan a ese saldo. ')
    if filters.get('view_mode') == 'Detalle':
        message += _('La columna Importe muestra cada movimiento: en el core, débito positivo y crédito negativo. '
            'Al pie se totaliza cada concepto de gestión por separado y el saldo contable suma solo las líneas del core. '
            'Los totales corresponden a los filtros del reporte. ')
    message += '<details><summary>' + _('Cómo comparar con el Excel') + '</summary>' + _(
        'Seleccione Contabilidad del core y use la misma empresa, cuenta, moneda y fechas del Excel. '
        'Se incluyen los movimientos originales importados, aunque estén conciliados, ignorados o cancelados dentro de la app; '
        'una cancelación interna no revierte un asiento del core. Los vínculos duplicados entre importación, partida y origen del depósito '
        'se representan una sola vez; líneas físicas distintas se conservan. '
        'Se usa el crédito contable original del depósito, que puede diferir del importe bancario bruto. '
        'US$ suma débitos y créditos convertidos y redondeados por línea con la tasa de origen/importación; '
        'C$ conserva los importes originales NIO. Sin conversión, el total afectado queda sin determinar. '
        'Desde/Hasta limitan movimientos: con Desde no se incorpora un saldo anterior a esa fecha. '
        'Los filtros de cuenta y moneda original aplican solo a Contabilidad del core; Gestión conserva el alcance por empresa. '
        'Conciliar, documentar un saldo a favor, compensar o registrar una CxC manual no cambia el saldo contable. '
        'Gestión muestra los remanentes por atender, incluyendo devoluciones, aplicaciones, compensaciones y reversiones. '
        'Los saldos a favor, CxC y depósitos sin asignar se muestran positivos; Core por conciliar conserva el signo débito/crédito. '
        'No existe un total que sume contabilidad y gestión. Una partida puede tener evidencia contable y gestión en secciones distintas. '
        'Sin registro cuenta complementarias manuales pendientes de asiento, incluso si su gestión ya terminó. '
        'El reporte incluye evidencia importada visible para su usuario; las líneas ausentes del sistema deben identificarse al cotejar el Excel.') + '</details>'
    if cutoff:
        message += '<br>' + cutoff_message(cutoff)
    if filters.get('cutoff_date'):
        data = [dict(row, cutoff_date=str(filters['cutoff_date']),
                     cutoff_warning='; '.join(cutoff['warnings']) if cutoff else '') for row in data]
    missing = sum(any(row.get(field) is None for field in CORE_FIELDS) if row['statement_scope'] == CORE
                  else row.get('management_amount_usd') is None for row in detail)
    if missing:
        message += _(' Hay {0} filas con importes sin determinar; no se presentan como cero.').format(missing)
    return get_columns(filters), data, message


def _presentation(detail, filters):
    """Offer actual states without another ledger query or losing other options."""
    states = sorted({row['status'] for row in detail if row.get('status')})
    selected = [row for row in detail if matches(row, filters)]
    data = _detail_presentation(selected) if filters.get('view_mode') == 'Detalle' else summarize(selected)
    if data:
        data[0]['_available_statuses'] = states
    return data


def _detail_presentation(selected):
    """One amount column and independent footer totals; never net management against core."""
    data = [dict(row,
        statement_amount_usd=row.get('core_balance_usd') if row['statement_scope'] == CORE else row.get('management_amount_usd'),
        statement_amount_nio=row.get('core_balance_nio') if row['statement_scope'] == CORE else None,
    ) for row in selected]

    def total(rows, field):
        values = [row.get(field) for row in rows]
        return None if any(value is None for value in values) else money_float(sum((money(value) for value in values), money(0)))

    # Management has several different meanings; a grand total would double count
    # items already represented in the ledger. Keep each component independent.
    for field, label in MANAGEMENT_METRICS:
        rows = [row for row in selected if row['statement_scope'] == MANAGEMENT and row.get('_component') == field]
        if rows:
            amount = total(rows, field)
            data.append(dict(_is_statement_total=True, _total_component=field, statement_scope=MANAGEMENT,
                client_name=_('Total: {0}').format(_(label)), position_type=_('Total de gestión'),
                usd_currency='USD', nio_currency='NIO', statement_amount_usd=amount,
                management_amount_usd=amount, **{field: amount}))
    rows = [row for row in selected if row['statement_scope'] == CORE]
    if rows:
        amounts = {field: total(rows, field) for field in CORE_FIELDS}
        data.append(dict(_is_statement_total=True, _total_component='core_balance_usd', statement_scope=CORE,
            client_name=_('Saldo contable del core'), position_type=_('Débitos − créditos'),
            usd_currency='USD', nio_currency='NIO', statement_amount_usd=amounts['core_balance_usd'],
            statement_amount_nio=amounts['core_balance_nio'], **amounts))
    return data


def get_columns(filters=None):
    section = selected_scope(filters or {})
    columns = [{'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link',
                'options': 'CN Employer', 'width': 200}]
    columns += [{'fieldname': field, 'label': _(label), 'fieldtype': 'Currency',
                 'options': 'nio_currency' if field.endswith('_nio') else 'usd_currency', 'precision': 2, 'width': 180}
                for field, label in (*CORE_METRICS, *MANAGEMENT_METRICS)]
    if (filters or {}).get('view_mode') == 'Detalle':
        for column in columns:
            if column['fieldname'] in {'core_balance_usd', 'core_balance_nio'}:
                column['label'] = _('Neto de la línea core US$' if column['fieldname'].endswith('_usd') else 'Neto de la línea core C$')
        columns += [
            {'fieldname': 'statement_amount_usd', 'label': _('Importe US$'), 'fieldtype': 'Currency',
             'options': 'usd_currency', 'precision': 2, 'width': 145},
            {'fieldname': 'statement_amount_nio', 'label': _('Importe original C$'), 'fieldtype': 'Currency',
             'options': 'nio_currency', 'precision': 2, 'width': 155},
            {'fieldname': 'statement_scope', 'label': _('Sección'), 'fieldtype': 'Data', 'width': 170},
            {'fieldname': 'management_amount_usd', 'label': _('Importe por gestionar US$'), 'fieldtype': 'Currency',
             'options': 'usd_currency', 'precision': 2, 'width': 155},
            {'fieldname': 'event_date', 'label': _('Fecha'), 'fieldtype': 'Date', 'width': 105},
            {'fieldname': 'position_type', 'label': _('Tipo'), 'fieldtype': 'Data', 'width': 195},
            {'fieldname': 'origin_type', 'label': _('Origen'), 'fieldtype': 'Data', 'width': 155},
            {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 220},
            {'fieldname': 'client_number', 'label': _('Nro. Cliente'), 'fieldtype': 'Data', 'width': 100},
            {'fieldname': 'loan_number', 'label': _('Nro. Crédito'), 'fieldtype': 'Data', 'width': 115},
            {'fieldname': 'source_doctype', 'label': _('Tipo de origen'), 'fieldtype': 'Data', 'hidden': 1},
            {'fieldname': 'source_document', 'label': _('Documento de origen'), 'fieldtype': 'Dynamic Link',
             'options': 'source_doctype', 'width': 200},
            {'fieldname': 'period', 'label': _('Período'), 'fieldtype': 'Link', 'options': 'CN Reconciliation Period', 'width': 170},
            *[{'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
              for field, label, width in [('status', 'Estado de conciliación', 190), ('category', 'Concepto', 170), ('subcategory', 'Subcategoría', 170), ('reason_type', 'Motivo', 165),
                  ('related_deposits', 'Depósitos relacionados', 195), ('treatment_status', 'Tratamiento', 200),
                  ('accounting_status', 'Registro contable', 170), ('management_status', 'Gestión externa', 170),
                  ('reference', 'Referencia / asiento', 155), ('source_rows', 'Filas de origen', 120),
                  ('observation', 'Observaciones', 350)]],
            *[{'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
              for field, label, width in [('source_account', 'Cuenta contable', 145), ('source_currency', 'Moneda original', 110),
                  ('source_file', 'Archivo original', 230), ('source_hash', 'Huella del archivo', 200),
                  ('source_row_id', 'ID de fila contable', 160), ('evidence_key', 'Identidad de línea contable', 200)]],
            {'fieldname': 'fx_rate', 'label': _('Tasa C$/US$ original'), 'fieldtype': 'Float', 'precision': 8, 'width': 140},
        ]
        # The compact view keeps a single amount; expanded columns retain the audit detail.
        leading = ('employer', 'event_date', 'client_name', 'client_number', 'loan_number',
                   'statement_scope', 'position_type', 'statement_amount_usd', 'origin_type', 'accounting_status', 'source_document')
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    else:
        columns.insert(1, {'fieldname': 'core_movement_count', 'label': _('Movimientos del core'), 'fieldtype': 'Int', 'width': 140})
        columns.insert(1, {'fieldname': 'pending_count', 'label': _('Partidas pendientes'), 'fieldtype': 'Int', 'width': 130})
        columns.insert(2, {'fieldname': 'accounting_pending_count', 'label': _('Complementarias sin registro'),
                           'fieldtype': 'Int', 'width': 180})
    if (filters or {}).get('cutoff_date'):
        columns += [
            {'fieldname': 'cutoff_date', 'label': _('Fecha de corte histórico'), 'fieldtype': 'Date', 'width': 150},
            {'fieldname': 'cutoff_warning', 'label': _('Advertencias del corte'), 'fieldtype': 'Data', 'width': 350},
        ]
    if str((filters or {}).get('compact_view', '0')) in {'1', 'True'}:
        if (filters or {}).get('view_mode') == 'Detalle':
            visible = ('employer', 'event_date', 'client_name', 'statement_scope', 'position_type',
                       'statement_amount_usd',
                       'status', 'origin_type', 'accounting_status', 'source_document')
            by_field = {column['fieldname']: column for column in columns}
            columns = [by_field[field] for field in visible] + [column for column in columns if column['fieldname'] not in visible]
            for column in columns:
                if column['fieldname'] not in visible:
                    column['hidden'] = 1
        for column in columns:
            if column['fieldname'] == 'employer':
                column['width'] = 180
            elif column['fieldname'] == 'pending_count':
                column['width'] = 100
            elif column['fieldname'] == 'accounting_pending_count':
                column['width'] = 110
            elif column['fieldtype'] == 'Currency':
                column['width'] = 140
        if (filters or {}).get('view_mode') != 'Detalle':
            labels = dict(application_pending_usd='Aplicaciones US$', core_pending_usd='Core US$',
                company_credit_usd='A favor empresa US$', client_credit_usd='A favor clientes US$',
                deposit_pending_usd='Depósitos US$', company_receivable_usd='CxC empresa US$', employee_receivable_usd='CxC empleados US$',
                core_balance_usd='Saldo contable core US$', pending_count='Pendientes de gestión',
                accounting_pending_count='Sin registro')
            for column in columns:
                if column['fieldname'] in labels:
                    column['label'] = _(labels[column['fieldname']])
                    column['width'] = 110 if column['fieldtype'] == 'Currency' else 90
            by_field = {column['fieldname']: column for column in columns}
            leading = ('employer', 'core_debit_usd', 'core_credit_usd', 'core_balance_usd',
                       'company_credit_usd', 'client_credit_usd', 'company_receivable_usd', 'employee_receivable_usd',
                       'core_movement_count', 'pending_count', 'accounting_pending_count')
            if section == MANAGEMENT:
                leading += ('application_pending_usd', 'core_pending_usd', 'deposit_pending_usd')
            columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
            for column in columns:
                if column['fieldname'] not in leading:
                    column['hidden'] = 1
        if (filters or {}).get('view_mode') == 'Detalle':
            widths = dict(employer=150, client_name=200, position_type=105,
                          statement_amount_usd=145, status=145, origin_type=120,
                          accounting_status=155, source_document=180)
            for column in columns:
                if column['fieldname'] in widths:
                    column['width'] = widths[column['fieldname']]
    if str((filters or {}).get('component') or '').endswith('_nio'):
        for column in columns:
            if column['fieldname'] == 'statement_amount_nio' or (
                    (filters or {}).get('view_mode') != 'Detalle' and column['fieldname'].endswith('_nio')):
                column.pop('hidden', None)
            if column['fieldname'] == 'statement_amount_usd' and str((filters or {}).get('compact_view', '0')) in {'1', 'True'}:
                column['hidden'] = 1
    if section == CORE:
        excluded = {*MANAGEMENT_FIELDS, 'management_amount_usd', 'pending_count', 'accounting_pending_count'}
    elif section == MANAGEMENT:
        excluded = {*CORE_FIELDS, 'core_movement_count', 'statement_amount_nio'}
    else:
        excluded = set()
    columns = [column for column in columns if column['fieldname'] not in excluded]
    return columns
