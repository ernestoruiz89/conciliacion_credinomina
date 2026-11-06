"""A compact, exportable client statement of one reconciliation period."""
import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.parsers import collection_comment
from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.reconciliation_report import add_portfolio_status, build_views, load_direct_applications
from credinomina_reconciliation.reconciliation_deposit_detail import load as load_deposit_details


def load_detail(period):
    """Load this report's receipt context independently of Resumen de Conciliacion."""
    data = period.as_dict()
    data['collection_cycle'] = data.get('collection_cycle') or ('Mensual' if data.get('reconciliation_mode') != 'Historica' else '')
    data['closure_state'] = 'Cerrado' if data.get('status') == 'Cerrado' else 'Abierto'
    data['usd_currency'] = 'USD'
    collections = [] if data.get('reconciliation_mode') == 'Historica' else (data.get('collection_rows') or [])
    details = build_views([data], collections, load_direct_applications([period.name]), load_deposit_details([data]))[1]
    add_portfolio_status([data], details)
    for row in details:
        comments = []
        for field, label in (('comments', 'Comentarios'), ('application_comment', 'Aplicación'),
                             ('cxc_difference_comment', 'Diferencias CXC')):
            text = collection_comment(row.get(field))
            if text:
                comments.append(f'{_(label)}: {text}')
        row['comments'] = '\n'.join(comments)
    return details


def project(details, period, filters=None):
    filters = filters or {}
    cycle = period.get('collection_cycle') or (
        period.get('historical_scope') if period.get('reconciliation_mode') == 'Historica' else 'Mensual') or ''
    cycle = {'Primera quincena': 'I QNA', 'Segunda quincena': 'II QNA'}.get(cycle, cycle)
    unavailable = any(detail.get('_detail_unavailable') for detail in details)
    result = []
    for detail in details:
        # Company documentation remains explicitly separate from named customers.
        if detail.get('_detail_unavailable'):
            continue
        bank, applied, pending = (detail.get(field) for field in
                                 ('detail_received_usd', 'application_usd', 'detail_unassigned_usd'))
        observations = [collection_comment(detail.get('difference_reason')), collection_comment(detail.get('observations'))]
        if detail.get('_company_conversion') and detail.get('conversion_usd') is not None:
            observations.append(f"Diferencia documentada de empresa US$ {money(detail['conversion_usd']):.2f}.")
        if unavailable:
            bank = pending = None
            observations.append('Importe bancario no determinable: falta acceso al detalle de depósitos.')
        if bank == 0 and money(detail.get('deposit_usd')) > 0 and not detail.get('_company_conversion'):
            bank = pending = None
            observations.append('Hay depósito asignado, pero no se dispone de un importe individual del detalle bancario verificable.')
        state = ('Revisar' if detail.get('has_difference') or any(value is None for value in (bank, applied, pending))
                 or money(pending) or money(detail.get('open_usd')) else 'Conciliado')
        comments = [collection_comment(detail.get('comments'))]
        for field, label in (('first_exception_comment', 'Primera conciliación'),
                             ('deposit_difference_notes', 'Diferencia del depósito')):
            text = collection_comment(detail.get(field))
            if text:
                comments.append(f'{label}: {text}')
        comment = '\n'.join(dict.fromkeys(text for text in comments if text))
        row = dict(client_name=detail.get('client_name'), employer=detail.get('employer'),
            client_number=detail.get('client_number') or '',
            loan_number=detail.get('loan_number_display') or detail.get('loan_number') or '',
            bank_usd=bank, applied_usd=applied, pending_usd=pending, state=state,
            credit_status=detail.get('portfolio_credit_status') or 'Sin corte de cartera aplicable',
            comment=comment, detail='\n'.join(dict.fromkeys(text for text in observations if text)),
            qna=cycle, period=period['name'], usd_currency='USD')
        if filters.get('state') and row['state'] != filters['state']:
            continue
        if filters.get('credit_status') and str(filters['credit_status']).strip().casefold() != row['credit_status'].casefold():
            continue
        if cint(filters.get('only_pending')) and pending is not None and money(pending) <= 0:
            continue
        search = str(filters.get('search') or '').strip().casefold()
        if search and not any(search in str(row[field] or '').casefold() for field in ('client_name', 'client_number', 'loan_number', 'comment', 'detail')):
            continue
        result.append(row)
    result.sort(key=lambda row: (str(row['client_name'] or '').casefold(), str(row['loan_number'])))
    for index, row in enumerate(result, 1):
        row['row_number'] = index
    return result


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get('period'):
        frappe.throw(_('Seleccione un período para consultar el reporte.'))
    period = frappe.get_doc('CN Reconciliation Period', filters.period)
    period.check_permission('read')
    details = load_detail(period)
    message = None
    rows = project(details, period.as_dict(), filters)
    incomplete = any(detail.get('_detail_unavailable') for detail in details) or any(
        row[field] is None for row in rows for field in ('bank_usd', 'applied_usd', 'pending_usd'))
    if incomplete:
        message = _('Totales omitidos: hay importes no determinables. Revise la columna Detalle.')
    totals = [] if incomplete else [dict(label=_(label), value=money_float(sum_money(row[field] for row in rows)),
        datatype='Currency', currency='USD') for field, label in (
            ('bank_usd', 'Banco US$'), ('applied_usd', 'Aplicado US$'), ('pending_usd', 'Importe pendiente US$'))]
    return get_columns(), rows, message, None, totals, True


def get_columns():
    return [
        dict(fieldname='row_number', label=_('N.º'), fieldtype='Int', width=50),
        dict(fieldname='client_name', label=_('Nombre del cliente'), fieldtype='Data', width=275),
        dict(fieldname='employer', label=_('Convenio'), fieldtype='Link', options='CN Employer', width=160),
        dict(fieldname='loan_number', label=_('Nro. Crédito'), fieldtype='Data', width=125),
        *[dict(fieldname=field, label=_(label), fieldtype='Currency', options='usd_currency', width=145)
          for field, label in (('bank_usd', 'Banco US$'), ('applied_usd', 'Aplicado US$'), ('pending_usd', 'Importe pendiente US$'))],
        dict(fieldname='state', label=_('Estado'), fieldtype='Data', width=105),
        dict(fieldname='credit_status', label=_('Estado del crédito'), fieldtype='Data', width=190),
        dict(fieldname='comment', label=_('Comentario'), fieldtype='Data', width=300),
        dict(fieldname='detail', label=_('Detalle'), fieldtype='Data', width=320),
        dict(fieldname='qna', label=_('QNA'), fieldtype='Data', width=130),
    ]
