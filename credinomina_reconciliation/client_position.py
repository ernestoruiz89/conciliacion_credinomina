"""Current reconciliation position: independent obligations, never a loan ledger."""
import frappe
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.application_aging import application_balances, balance_status
from credinomina_reconciliation.complementary_balances import FIELDS, load_balances, cached_balance_reads, reconciliation_state
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _matches(row, filters):
    for field in ('employer', 'client_number', 'national_id', 'loan_number', 'collection_cycle', 'operational_status',
                  'category', 'subcategory', 'reason_type', 'accounting_status', 'management_status', 'treatment_status'):
        if filters.get(field) and str(row.get(field) or '') != str(filters[field]):
            return False
    when = row.get('event_date')
    if filters.get('from_month') and (not when or getdate(when) < getdate(filters['from_month'])):
        return False
    if filters.get('to_month') and (not when or getdate(when) > getdate(filters['to_month'])):
        return False
    return not filters.get('only_open') or row.get('is_open')


def build_position(collections, applications, items, balances, clients, filters, *, adjustment_receivables=()):
    output = []
    for source in collections:
        row = dict(source, position_type='Cobranza', event_date=source.get('payroll_month'),
            source_doctype='CN Reconciliation Period', source_document=source.get('period'),
            is_open=not source.get('operational_status', '').startswith(('Conforme', 'Conciliado')))
        # Applications and their cash are shown only in application rows below.
        for field in ('applied_usd', 'applied_nio', 'remitted_usd', 'remitted_nio',
                      'complementary_usd', 'fx_variance_usd', 'rounding_adjustment_usd'):
            row[field] = None
        output.append(row)
    for source in applications:
        state = balance_status(source)
        output.append(dict(source, position_type='Aplicación', event_date=source.get('application_date') or source.get('payroll_month'),
            source_doctype='CN Accounting Import' if source.get('source_import') else 'CN Reconciliation Period',
            source_document=source.get('source_import') or source.get('period'),
            remitted_usd=source.get('paid_usd'), applied_pending_usd=source.get('amount_usd'),
            rounding_adjustment_usd=source.get('adjustment_usd'),
            operational_status=state, is_open=state != 'Conciliado'))
    by_number = {}
    for client in clients:
        key = (client.get('employer'), client.get('client_number'))
        by_number.setdefault(key, []).append(client)
    by_name = {client['name']: client for client in clients}
    for item in items:
        balance = balances[item['name']]
        candidates = by_number.get((item.get('employer'), item.get('client_number')), [])
        client = by_name.get(item.get('credit_client')) or (candidates[0] if len(candidates) == 1 else {})
        # A company-wide amount is not allocated to individual clients by inference.
        company_wide = item.get('category') == 'Saldo a favor de la empresa'
        if company_wide:
            client = {}
        if item.get('docstatus') == 2:
            continue
        financial_pending = (balance.get('pending_usd') is None or bool(money(balance.get('pending_usd')))
                             or balance['financial_status'] == 'Revisar distribución')
        management_pending = bool(money(balance.get('management_pending_usd')))
        accounting_pending = balance.get('accounting_status') in {'Pendiente de registro', 'Asiento informado'}
        if balance['financial_status'] in {'Cancelada', 'Revertida', 'No conciliatoria'}:
            accounting_pending = False
        output.append(dict(
            position_type='Partida complementaria', event_date=item.get('posting_date'),
            source_doctype='CN Complementary Item', source_document=item['name'],
            employer=item.get('employer'), period=item.get('period'),
            client_number='' if company_wide else item.get('client_number') or client.get('client_number'),
            client_name='Sin cliente individual' if company_wide else client.get('client_name') or item.get('client_name') or item.get('source_client_name'),
            national_id=client.get('national_id'), loan_number='' if company_wide else item.get('loan_number'),
            category=item.get('category'), subcategory=item.get('subcategory'), reason_type=item.get('reason_type'),
            treatment_status=balance['financial_status'], complementary_usd=balance['original_usd'],
            complementary_used_usd=balance['used_usd'], complementary_pending_usd=balance['pending_usd'],
            credit_pending_usd=balance['management_pending_usd'], credit_resolved_usd=item.get('credit_resolved_usd'),
            accounting_status=balance['accounting_status'], operational_status=reconciliation_state(balance),
            management_status=balance.get('management_status'),
            observation='; '.join(filter(None, [balance.get('amount_issue'), item.get('description')])),
            is_open=financial_pending or management_pending or accounting_pending,
            usd_currency='USD', nio_currency='NIO'))
    for item in adjustment_receivables:
        output.append(dict(position_type='CxC por ajuste', event_date=item.get('posting_date'),
            source_doctype='CN Complementary Item', source_document=item['name'], employer=item.get('employer'),
            period=item.get('period'), client_name=item.get('client_name'), client_number=item.get('client_number'),
            loan_number=item.get('loan_number'), category=item.get('category'), subcategory=item.get('subcategory'),
            reason_type=item.get('reason_type'),
            company_receivable_usd=item['receivable_usd'], operational_status=item['receivable_status'],
            accounting_status=item.get('accounting_status'), management_status=item['receivable_status'],
            observed_usd=item.get('observed_usd'),
            observation='; '.join(filter(None, [item.get('observation'), item.get('description')])),
            is_open=True, usd_currency='USD'))
    view = filters.get('position_type')
    return sorted([row for row in output if (not view or row['position_type'] == view) and _matches(row, filters)],
                  key=lambda row: (str(row.get('event_date') or ''), row['position_type'],
                                   row.get('client_name') or '', row.get('source_document') or ''))


@cached_balance_reads
def load_position(collections, filters):
    if filters.get('from_month') and filters.get('to_month') and getdate(filters['from_month']) > getdate(filters['to_month']):
        frappe.throw('La fecha Desde no puede ser posterior a Hasta.')
    view = filters.get('position_type')
    applications = []
    if not view or view == 'Aplicación':
        inputs = load_application_context(employer=filters.get('employer'))
        applications = application_balances(*inputs, nowdate(), include_settled=True)
    fields = list(dict.fromkeys([*FIELDS, 'client_number', 'loan_number', 'credit_client', 'subcategory', 'reason_type',
        'client_name', 'source_client_name', 'credit_resolved_usd']))
    scope = {'employer': filters['employer']} if filters.get('employer') else {}
    items = list(records('CN Complementary Item', fields=fields, filters={**scope, 'docstatus': ['!=', 2]})) if not view or view == 'Partida complementaria' else []
    clients = list(records('CN Client', filters=scope, fields=['name', 'client_number', 'client_name', 'national_id', 'employer'])) if items else []
    from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
    return build_position(collections, applications, items, load_balances(items), clients, filters,
        adjustment_receivables=load_receivables(employer=filters.get('employer')) if not view or view == 'CxC por ajuste' else [])


def summary(data, filters=None):
    """Totals of the selected rows, only for the selected financial dimensions.

    An unknown amount suppresses that monetary total instead of silently adding
    zero. Signed complementary balances are magnitudes of separate obligations,
    never a net against customer credits or application receivables.
    """
    view = (filters or {}).get('position_type')
    kinds = {view} if view else {row['position_type'] for row in data}
    figures = []

    def count(label, value, color='orange'):
        figures.append(dict(label=label, value=value, datatype='Int', indicator=color))

    def total(rows, field, label, color='blue', unknown=None):
        missing = sum(unknown(row) if unknown else row.get(field) is None for row in rows)
        if missing:
            count(label + ' · importes sin determinar', missing, 'red')
        else:
            figures.append(dict(label=label, value=money_float(sum_money(row.get(field) for row in rows)),
                                datatype='Currency', currency='USD', indicator=color))

    if 'Cobranza' in kinds:
        rows = [row for row in data if row['position_type'] == 'Cobranza']
        total(rows, 'expected_usd', 'Cobranza US$', unknown=lambda row:
              row.get('expected_usd') is None or (not money(row.get('expected_usd')) and bool(money(row.get('expected_nio')))))
        total(rows, 'quality_applied_usd', 'Aplicado para control US$')
        count('Filas con diferencias de control', sum(row.get('is_open', False) for row in rows))
    if 'Aplicación' in kinds:
        rows = [row for row in data if row['position_type'] == 'Aplicación']
        total(rows, 'applied_usd', 'Aplicado neto US$')
        total(rows, 'remitted_usd', 'Depósito asignado US$', 'green')
        total(rows, 'applied_pending_usd', 'CxC por aplicaciones US$', 'orange')
    if 'Partida complementaria' in kinds:
        rows = [row for row in data if row['position_type'] == 'Partida complementaria']
        reviews = [row for row in rows if row.get('operational_status') == 'Revisar distribución'
                   or (row.get('complementary_pending_usd') is not None and money(row['complementary_pending_usd']) < 0)]
        review_ids = {id(row) for row in reviews}
        valid = [row for row in rows if id(row) not in review_ids]
        total(valid, 'complementary_pending_usd', 'Complementarias pendientes de conciliar US$', 'orange')
        if reviews:
            count('Partidas con inconsistencias', len(reviews), 'red')
            observed = [dict(row, observed_usd=abs(money(row.get('complementary_pending_usd')))
                             or abs(money(row.get('complementary_usd')))) for row in reviews]
            total(observed, 'observed_usd', 'Importes observados US$', 'red')
        # None means this dimension does not apply to non-credit complementary items.
        credits = [row for row in rows if row.get('category') in {'Saldo a favor del cliente', 'Saldo a favor de la empresa'}]
        if credits or view == 'Partida complementaria':
            total(credits, 'credit_pending_usd', 'Saldos a favor por gestionar US$')
        count('Partidas pendientes de registro contable', sum(
            row.get('accounting_status') in {'Pendiente de registro', 'Asiento informado'} for row in rows))
    if 'CxC por ajuste' in kinds:
        total([row for row in data if row['position_type'] == 'CxC por ajuste'],
              'company_receivable_usd', 'CxC por ajustes US$', 'orange')
    return figures
