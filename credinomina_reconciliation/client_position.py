"""Current reconciliation position: independent obligations, never a loan ledger."""
import frappe
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.application_context import load_application_context
from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.complementary_balances import FIELDS, load_balances
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _matches(row, filters):
    for field in ('employer', 'client_number', 'national_id', 'loan_number', 'collection_cycle'):
        if filters.get(field) and str(row.get(field) or '') != str(filters[field]):
            return False
    when = row.get('event_date')
    if filters.get('from_month') and (not when or getdate(when) < getdate(filters['from_month'])):
        return False
    if filters.get('to_month') and (not when or getdate(when) > getdate(filters['to_month'])):
        return False
    return not filters.get('only_open') or row.get('is_open')


def build_position(collections, applications, items, balances, clients, filters):
    output = []
    for source in collections:
        row = dict(source, position_type='Cobranza', event_date=source.get('payroll_month'),
            source_doctype='CN Reconciliation Period', source_document=source.get('period'),
            is_open=not source.get('operational_status', '').startswith('Conciliado'))
        # Applications and their cash are shown only in application rows below.
        for field in ('applied_usd', 'applied_nio', 'remitted_usd', 'remitted_nio',
                      'complementary_usd', 'fx_variance_usd', 'rounding_adjustment_usd'):
            row[field] = None
        output.append(row)
    for source in applications:
        available = source.get('amount_usd') is not None
        excess = (available and money(source.get('paid_usd')) >
                  money(source.get('applied_usd')) + money(source.get('adjustment_usd')))
        review = excess or bool(money(source.get('fx_variance_usd')))
        pending = not available or money(source.get('amount_usd')) > 0
        output.append(dict(source, position_type='Aplicación', event_date=source.get('application_date') or source.get('payroll_month'),
            source_doctype='CN Accounting Import' if source.get('source_import') else 'CN Reconciliation Period',
            source_document=source.get('source_import') or source.get('period'),
            remitted_usd=source.get('paid_usd'), applied_pending_usd=source.get('amount_usd'),
            rounding_adjustment_usd=source.get('adjustment_usd'),
            operational_status='Sin conversión US$' if not available else 'Revisar distribución' if review
                else 'Pendiente' if pending else 'Conciliado', is_open=pending or review))
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
        financial_pending = bool(money(balance.get('pending_usd')))
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
            category=item.get('category'), complementary_usd=balance['original_usd'],
            complementary_used_usd=balance['used_usd'], complementary_pending_usd=balance['pending_usd'],
            credit_pending_usd=balance['management_pending_usd'], credit_resolved_usd=item.get('credit_resolved_usd'),
            accounting_status=balance['accounting_status'], operational_status=balance['financial_status'],
            management_status=balance.get('management_status'), observation=item.get('description'),
            is_open=financial_pending or management_pending or accounting_pending,
            usd_currency='USD', nio_currency='NIO'))
    view = filters.get('position_type')
    return sorted([row for row in output if (not view or row['position_type'] == view) and _matches(row, filters)],
                  key=lambda row: (str(row.get('event_date') or ''), row['position_type'],
                                   row.get('client_name') or '', row.get('source_document') or ''))


def load_position(collections, filters):
    if filters.get('from_month') and filters.get('to_month') and getdate(filters['from_month']) > getdate(filters['to_month']):
        frappe.throw('La fecha Desde no puede ser posterior a Hasta.')
    inputs = load_application_context()
    applications = application_balances(*inputs, nowdate(), include_settled=True)
    fields = list(dict.fromkeys([*FIELDS, 'client_number', 'loan_number', 'credit_client',
        'client_name', 'source_client_name', 'credit_resolved_usd']))
    items = list(records('CN Complementary Item', fields=fields, filters={'docstatus': ['!=', 2]}))
    clients = list(records('CN Client', fields=['name', 'client_number', 'client_name', 'national_id', 'employer']))
    return build_position(collections, applications, items, load_balances(items), clients, filters)


def summary(data):
    # These are separate dimensions. No grand total or netting with client credits.
    figures = [{'label': label, 'value': money_float(sum_money(row.get(field) for row in data)),
             'datatype': 'Currency', 'currency': 'USD', 'indicator': color}
            for label, field, color in (
                ('CxC por aplicaciones US$', 'applied_pending_usd', 'orange'),
                ('Saldos a favor por gestionar US$', 'credit_pending_usd', 'blue'))]
    for label, count in (
        ('Cobranzas con deducción sin determinar', sum(row['position_type'] == 'Cobranza' and row.get('employee_pending_usd') is None for row in data)),
        ('Aplicaciones sin conversión US$', sum(row['position_type'] == 'Aplicación' and row.get('applied_pending_usd') is None for row in data)),
    ):
        if count:
            figures.append({'label': label, 'value': count, 'datatype': 'Int', 'indicator': 'red'})
    return figures
