import json
from html import escape

from frappe import _
from credinomina_reconciliation.rounding import money, money_float

from credinomina_reconciliation.company_statement import load_detail
from credinomina_reconciliation.deposit_difference_report import load as load_deposit_differences
from credinomina_reconciliation.company_accounting_statement import (
    CORE, MANAGEMENT, CORE_METRICS, MANAGEMENT_METRICS, CORE_FIELDS, MANAGEMENT_FIELDS, POSITION_METRICS, POSITION_FIELDS,
    load_core_detail, management_detail, matches, selected_scope, summarize, validate_dates,
)


def execute(filters=None):
    filters = filters or {}
    if filters.get('view_mode') == 'Conciliación':
        from credinomina_reconciliation.company_statement_bridge import execute as bridge
        return bridge(filters)
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
    if section != CORE and not filters.get('component'):
        differences = load_deposit_differences(scope)
        if str(filters.get('accounting_queue') or '0') in {'1', 'True'}:
            differences = [row for row in differences if row['accounting_pending']]
        detail.extend(differences)
    data = _presentation(detail, filters)
    message = (_('Saldo contable del core = débitos originales − créditos originales. ')
        if section == CORE else _('Saldo = pendientes − saldos a favor − otros créditos pendientes. '
        'Los saldos a favor y los créditos se presentan con signo negativo. '
        'Resumen y Detalle usan las mismas partidas y filtros; documentar un saldo a favor no duplica el crédito del depósito. '))
    if filters.get('view_mode') == 'Detalle':
        message += (_('Contabilidad del core muestra los movimientos completos: débitos positivos, créditos negativos y saldo contable al pie. ')
            if section == CORE else _('El detalle muestra cada pendiente o crédito una sola vez y su saldo neto al pie. '))
        message += _comparison_message(detail, filters)
    message += _('La vista Conciliación explica cualquier diferencia con el saldo contable e identifica el registro pendiente. ')
    if section != CORE:
        message += _('Diferencia de depósito pendiente en core es un control informativo separado: no se suma al saldo de gestión ni elimina pendientes de clientes. En cortes anteriores, su estado puede no ser determinable por falta de historial de vínculos. ')
    message += '<details><summary>' + _('Cómo comparar con el Excel') + '</summary>' + _(
        'Seleccione Contabilidad del core y use la misma empresa, cuenta, moneda y fechas del Excel. '
        'Se incluyen los movimientos originales importados, aunque estén conciliados, ignorados o cancelados dentro de la app; '
        'una cancelación interna no revierte un asiento del core. Los vínculos duplicados entre importación, partida y origen del depósito '
        'se representan una sola vez; líneas físicas distintas se conservan. '
        'Se usa el crédito contable original del depósito, que puede diferir del importe bancario bruto. '
        'US$ suma débitos y créditos convertidos y redondeados por línea con la tasa de origen/importación; '
        'C$ conserva los importes originales NIO. Sin conversión, el total afectado queda sin determinar. '
        'Desde/Hasta limitan movimientos: con Desde no se incorpora un saldo anterior a esa fecha. '
        'Los filtros de cuenta y moneda original aplican solo a Contabilidad del core; los pendientes conservan el alcance por empresa. '
        'Conciliar, documentar un saldo a favor, compensar o registrar una CxC manual no cambia el saldo contable. '
        'Gestión muestra los remanentes por atender, incluyendo devoluciones, aplicaciones, compensaciones y reversiones. '
        'Las aplicaciones y CxC pendientes son positivas; los saldos a favor y depósitos sin asignar son negativos. '
        'Core por conciliar conserva el signo débito/crédito. Otros créditos pendientes agrupa los créditos del core '
        'por conciliar y los depósitos sin asignar. Pendientes agrupa los importes positivos antes de descontar los créditos. '
        'En Detalle se consulta una base por vez: pendientes o movimientos contables completos. '
        'El saldo contable se compara con el saldo de pendientes; nunca se suma a él. '
        'Sin registro incluye complementarias manuales y diferencias de depósito pendientes en el core. '
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
    section = CORE if selected_scope(filters) == CORE else MANAGEMENT
    states = sorted({row['status'] for row in detail if row.get('status') and row['statement_scope'] == section})
    selected = [row for row in detail if matches(row, filters)]
    data = _detail_presentation(selected, filters) if filters.get('view_mode') == 'Detalle' else summarize(selected)
    if data:
        data[0]['_available_statuses'] = states
    return data


def _detail_presentation(selected, filters):
    """Show one population and one net total, never full deposits plus their reserves."""
    section = CORE if selected_scope(filters) == CORE else MANAGEMENT
    rows = [row for row in selected if row['statement_scope'] == section]
    data = [dict(row,
        statement_amount_usd=row.get('core_balance_usd') if row['statement_scope'] == CORE else row.get('management_amount_usd'),
        statement_amount_nio=row.get('core_balance_nio') if row['statement_scope'] == CORE else None,
    ) for row in rows]

    def total(rows, field):
        values = [row.get(field) for row in rows]
        return None if any(value is None for value in values) else money_float(sum((money(value) for value in values), money(0)))

    if section == CORE and rows:
        amounts = {field: total(rows, field) for field in CORE_FIELDS}
        data.append(dict(_is_statement_total=True, _total_component='core_balance_usd', statement_scope=CORE,
            client_name=_('Saldo contable del core'), position_type=_('Débitos − créditos'),
            usd_currency='USD', nio_currency='NIO', statement_amount_usd=amounts['core_balance_usd'],
            statement_amount_nio=amounts['core_balance_nio'], **amounts))
    elif section == MANAGEMENT and (rows or (selected and _comparison_available(filters))):
        # A fully settled company may still have core evidence, hence a zero
        # pending total rather than a fabricated second set of cash movements.
        amount = total(rows, 'management_amount_usd')
        amounts = {field: total(rows, field) for field in MANAGEMENT_FIELDS}
        data.append(dict(_is_statement_total=True, _total_component='management_net_usd', statement_scope=MANAGEMENT,
            client_name=_('Saldo'), position_type=_('Pendientes menos créditos'), usd_currency='USD', nio_currency='NIO',
            statement_amount_usd=amount, management_net_usd=amount, management_amount_usd=amount, **amounts))
    return data


def _comparison_available(filters):
    from credinomina_reconciliation.company_statement_bridge import SCOPED_FILTERS
    return selected_scope(filters) == 'Todos' and not any(filters.get(field) for field in SCOPED_FILTERS)


def _comparison_message(detail, filters):
    if not detail or not _comparison_available(filters):
        return ''
    overall = summarize(dict(row, employer=None) for row in detail)[0]
    def display(field):
        value = overall[field]
        return _('Sin determinar') if value is None else f'US$ {value:,.2f}'
    args = escape(json.dumps(dict(view_mode='Conciliación', employer=filters.get('employer') or '')), quote=True)
    return ('<div class="my-2"><strong>' + _('Comparación con el core') + '</strong>: '
        + _('Saldo contable') + ' ' + display('core_balance_usd') + ' · '
        + _('Saldo de pendientes') + ' ' + display('management_net_usd') + ' · '
        + _('Diferencia') + ' ' + display('reconciliation_difference_usd') + ' '
        + f'<button type="button" class="btn btn-xs btn-default cn-statement-detail" data-detail="{args}">'
        + _('Ver diferencias y registro pendiente') + '</button></div>')


def get_columns(filters=None):
    if (filters or {}).get('view_mode') == 'Conciliación':
        from credinomina_reconciliation.company_statement_bridge import get_columns as bridge_columns
        return bridge_columns(filters)
    section = selected_scope(filters or {})
    columns = [{'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link',
                'options': 'CN Employer', 'width': 200}]
    columns += [{'fieldname': field, 'label': _(label), 'fieldtype': 'Currency',
                 'options': 'nio_currency' if field.endswith('_nio') else 'usd_currency', 'precision': 2, 'width': 180}
                for field, label in (*CORE_METRICS, *MANAGEMENT_METRICS)]
    columns.append({'fieldname': 'pending_core_difference_usd', 'label': _('Diferencia depósito pendiente en core US$'),
                    'fieldtype': 'Currency', 'options': 'usd_currency', 'precision': 2, 'width': 205})
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
              for field, label, width in [('status', 'Estado de conciliación', 190), ('category', 'Concepto', 170), ('subcategory', 'Subcategoría', 170), ('reason_type', 'Motivo', 165), ('difference_reason', 'Razón de la diferencia', 190),
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
                   'statement_scope', 'position_type', 'statement_amount_usd', 'pending_core_difference_usd', 'origin_type', 'accounting_status', 'source_document')
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    else:
        columns += [dict(fieldname=field, label=_(label), fieldtype='Currency', options='usd_currency', precision=2, width=180)
            for field, label in (*POSITION_METRICS, ('reconciliation_difference_usd', 'Diferencia saldo − core US$'))]
        columns.insert(1, {'fieldname': 'core_movement_count', 'label': _('Movimientos del core'), 'fieldtype': 'Int', 'width': 140})
        columns.insert(1, {'fieldname': 'pending_count', 'label': _('Partidas pendientes'), 'fieldtype': 'Int', 'width': 130})
        columns.insert(2, {'fieldname': 'accounting_pending_count', 'label': _('Pendientes de registro'),
                           'fieldtype': 'Int', 'width': 180})
        leading = (('employer', 'core_debit_usd', 'core_credit_usd', 'core_balance_usd', 'core_movement_count') if section == CORE else
            ('employer', 'management_pending_usd', 'client_credit_usd', 'company_credit_usd', 'other_credit_usd',
             'management_net_usd', 'core_balance_usd', 'reconciliation_difference_usd', 'pending_core_difference_usd', 'pending_count', 'accounting_pending_count'))
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    if (filters or {}).get('cutoff_date'):
        columns += [
            {'fieldname': 'cutoff_date', 'label': _('Fecha de corte histórico'), 'fieldtype': 'Date', 'width': 150},
            {'fieldname': 'cutoff_warning', 'label': _('Advertencias del corte'), 'fieldtype': 'Data', 'width': 350},
        ]
    if str((filters or {}).get('compact_view', '0')) in {'1', 'True'}:
        if (filters or {}).get('view_mode') == 'Detalle':
            visible = ('employer', 'event_date', 'client_name', 'statement_scope', 'position_type',
                       'statement_amount_usd', 'pending_core_difference_usd',
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
                company_credit_usd='A favor empresa (−) US$', client_credit_usd='A favor clientes (−) US$',
                deposit_pending_usd='Depósitos (−) US$', company_receivable_usd='CxC empresa US$', employee_receivable_usd='CxC empleados US$',
                core_balance_usd='Saldo contable core US$', pending_count='Pendientes de gestión',
                accounting_pending_count='Sin registro')
            for column in columns:
                if column['fieldname'] in labels:
                    column['label'] = _(labels[column['fieldname']])
                    column['width'] = 110 if column['fieldtype'] == 'Currency' else 90
            by_field = {column['fieldname']: column for column in columns}
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
        excluded = {*MANAGEMENT_FIELDS, 'management_amount_usd', 'pending_count', 'accounting_pending_count',
                    *POSITION_FIELDS, 'reconciliation_difference_usd', 'pending_core_difference_usd'}
    elif section == MANAGEMENT:
        excluded = {*CORE_FIELDS, 'core_movement_count', 'statement_amount_nio', 'reconciliation_difference_usd'}
    else:
        excluded = set()
    if (filters or {}).get('view_mode') == 'Detalle' and section != CORE:
        excluded.update((*CORE_FIELDS, 'core_movement_count', 'statement_amount_nio'))
    # A partial ledger cannot be compared with the full management population.
    from credinomina_reconciliation.company_statement_bridge import SCOPED_FILTERS
    if any((filters or {}).get(field) for field in SCOPED_FILTERS):
        excluded.add('reconciliation_difference_usd')
    columns = [column for column in columns if column['fieldname'] not in excluded]
    return columns
