from frappe import _

from credinomina_reconciliation.company_statement import METRICS, load_detail, summarize


def execute(filters=None):
    filters = filters or {}
    detail = load_detail(filters)
    data = detail if filters.get('view_mode') == 'Detalle' else summarize(detail)
    message = _('Saldos actuales en US$. Saldo neto informativo suma las columnas; los importes sin vincular conservan su gestión pendiente. '
        'Desde/Hasta filtran la fecha de origen. ') + '<details><summary>' + _('Cómo se calcula') + '</summary>' + _(''
        'Saldo suma las columnas con su signo; no ejecuta compensaciones. '
        'Las partidas pendientes del core incluyen Por clasificar: débito positivo y crédito negativo. '
        'Solo se resta el remanente, sin duplicar ajustes ya utilizados. '
        'Saldo por cobrar a la empresa: ajustes manuales negativos clasificados como CxC a la empresa y efectivamente distribuidos en depósitos; '
        'trasladan el faltante de las aplicaciones a la empresa. Su registro contable no equivale a cobro. '
        'Saldos a favor: documentados y aún por gestionar. Depósitos: importe sin asignar ni documentar como saldo a favor; '
        'incluye los importados del core sin confirmar, no los borradores manuales. '
        'Desde/Hasta filtran la fecha de origen, no la fecha de conciliación. '
        'Solo se muestran registros pendientes visibles para su usuario.') + '</details>'
    missing = sum(row['balance_usd'] is None for row in detail)
    if missing:
        message += _(' Hay {0} movimientos con importe o signo sin determinar: su saldo no se presenta como cero.').format(missing)
    return get_columns(filters), data, message


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
            {'fieldname': 'client_name', 'label': _('Cliente'), 'fieldtype': 'Data', 'width': 220},
            {'fieldname': 'client_number', 'label': _('Nro. Cliente'), 'fieldtype': 'Data', 'width': 100},
            {'fieldname': 'loan_number', 'label': _('Nro. Crédito'), 'fieldtype': 'Data', 'width': 115},
            {'fieldname': 'source_doctype', 'label': _('Tipo de origen'), 'fieldtype': 'Data', 'hidden': 1},
            {'fieldname': 'source_document', 'label': _('Documento de origen'), 'fieldtype': 'Dynamic Link',
             'options': 'source_doctype', 'width': 200},
            {'fieldname': 'period', 'label': _('Período'), 'fieldtype': 'Link', 'options': 'CN Reconciliation Period', 'width': 170},
            *[{'fieldname': field, 'label': _(label), 'fieldtype': 'Data', 'width': width}
              for field, label, width in [('status', 'Estado', 190), ('category', 'Concepto', 170),
                  ('related_deposits', 'Depósitos relacionados', 195), ('accounting_status', 'Registro contable', 170),
                  ('reference', 'Referencia / asiento', 155), ('source_rows', 'Filas de origen', 120),
                  ('observation', 'Observaciones', 350)]],
        ]
    return columns
