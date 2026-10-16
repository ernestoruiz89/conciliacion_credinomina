from frappe import _

from credinomina_reconciliation.company_statement import METRICS, load_detail, summarize


def execute(filters=None):
    filters = filters or {}
    scope = {key: value for key, value in filters.items() if key != 'status'}
    if filters.get('cutoff_date'):
        from credinomina_reconciliation.historical_cutoff import load_cutoff, cutoff_message
        result = load_cutoff(scope)
        data = _presentation(result['detail'], filters)
        # Keep the date and qualifications in native spreadsheet exports too.
        data = [dict(row, cutoff_date=result['cutoff_date'], cutoff_warning='; '.join(result['warnings'])) for row in data]
        return get_columns(filters), data, cutoff_message(result)
    detail = load_detail(scope)
    data = _presentation(detail, filters)
    message = _('Saldos actuales en US$. Saldo neto informativo suma las columnas; los importes sin vincular conservan su gestión pendiente. '
        'La columna Sin registro cuenta partidas complementarias manuales pendientes de registro, incluso si ya no tienen saldo financiero. '
        'Desde/Hasta filtran la fecha de origen. ') + '<details><summary>' + _('Cómo se calcula') + '</summary>' + _(''
        'Saldo suma las columnas con su signo; no ejecuta compensaciones. '
        'Las partidas pendientes del core incluyen Por clasificar: débito positivo y crédito negativo. '
        'Solo se resta el remanente, sin duplicar ajustes ya utilizados. '
        'Saldo por cobrar a la empresa: ajustes manuales negativos clasificados como CxC a la empresa y efectivamente distribuidos en depósitos; '
        'trasladan el faltante de las aplicaciones a la empresa. La CxC al Empleado identifica al cliente deudor y se muestra en su propia columna. '
        'Su registro contable no equivale a cobro. '
        'Saldos a favor: documentados y aún por gestionar. Depósitos: importe sin asignar ni documentar como saldo a favor; '
        'incluye los importados del core sin confirmar, no los borradores manuales. '
        'Desde/Hasta filtran la fecha de origen, no la fecha de conciliación. '
        'El detalle también muestra partidas complementarias sin otra fila financiera que siguen Pendiente de registro o Asiento informado; '
        'estas filas son de control contable y no modifican el saldo neto. Consulte la partida para conocer su saldo individual. '
        'Solo se muestran registros visibles para su usuario. '
        'Importe observado identifica evidencia inconsistente pendiente de revisión; no se suma al saldo neto.') + '</details>'
    missing = sum(row['balance_usd'] is None for row in detail)
    if missing:
        message += _(' Hay {0} movimientos con importe o signo sin determinar: su saldo no se presenta como cero.').format(missing)
    return get_columns(filters), data, message


def _presentation(detail, filters):
    """Offer actual states without another ledger query or losing other options."""
    states = sorted({row['status'] for row in detail if row.get('status')})
    selected = [row for row in detail if not filters.get('status') or row.get('status') == filters['status']]
    data = [dict(row) for row in selected] if filters.get('view_mode') == 'Detalle' else summarize(selected)
    if data:
        data[0]['_available_statuses'] = states
    return data


def get_columns(filters=None):
    columns = [{'fieldname': 'employer', 'label': _('Empresa'), 'fieldtype': 'Link',
                'options': 'CN Employer', 'width': 200}]
    columns += [{'fieldname': field, 'label': _(label), 'fieldtype': 'Currency',
                 'options': 'usd_currency', 'precision': 2, 'width': 180}
                for field, label in (*METRICS, ('balance_usd', 'Saldo neto informativo US$'))]
    if (filters or {}).get('view_mode') == 'Detalle':
        columns += [
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
        ]
        # Identify the person and source before the monetary matrix.
        leading = ('employer', 'event_date', 'client_name', 'client_number', 'loan_number',
                   'origin_type', 'accounting_status', 'source_document')
        by_field = {column['fieldname']: column for column in columns}
        columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
    else:
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
            visible = ('employer', 'client_name', 'position_type', 'balance_usd', 'status',
                       'origin_type', 'accounting_status', 'source_document')
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
                balance_usd='Saldo neto US$', pending_count='Pendientes',
                accounting_pending_count='Sin registro')
            for column in columns:
                if column['fieldname'] in labels:
                    column['label'] = _(labels[column['fieldname']])
                    column['width'] = 110 if column['fieldtype'] == 'Currency' else 90
            by_field = {column['fieldname']: column for column in columns}
            leading = ('employer', 'balance_usd', 'pending_count', 'accounting_pending_count')
            columns = [by_field[field] for field in leading] + [column for column in columns if column['fieldname'] not in leading]
        if (filters or {}).get('view_mode') == 'Detalle':
            widths = dict(employer=150, client_name=200, position_type=105,
                          balance_usd=110, status=145, origin_type=120,
                          accounting_status=155, source_document=180)
            for column in columns:
                if column['fieldname'] in widths:
                    column['width'] = widths[column['fieldname']]
                if column['fieldname'] == 'balance_usd':
                    column['label'] = _('Saldo US$')
    return columns
