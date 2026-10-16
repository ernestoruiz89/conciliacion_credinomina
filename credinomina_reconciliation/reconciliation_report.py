"""Two-stage report, with identical client/loan evidence for both views."""
from collections import defaultdict

import frappe
from frappe.utils import get_last_day, getdate

from credinomina_reconciliation.accounting_client_summary import entries
from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.credit_lookup import get_credit_rows
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.period_totals import direct_amounts_by_collection
from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.report_records import records, child_records, require_complete_evidence, PAGE_SIZE
from credinomina_reconciliation.rounding import money, money_float, sum_money


AMOUNTS = ('collection_usd', 'application_usd', 'deposit_usd', 'credit_used_usd',
           'cash_adjustment_usd', 'open_usd')
COMMENTS = ('comments', 'application_comment', 'first_exception_comment')


def add_portfolio_status(periods, details):
    """Read each credit's original status from the latest accessible monthly cut."""
    if not details:
        return
    for row in details:
        row['portfolio_credit_status'] = 'Sin corte de cartera aplicable'
    if not frappe.has_permission('CN Credit Portfolio Snapshot', 'read'):
        return
    months = {period['name']: get_last_day(period.get('cutoff_date') or period['payroll_month'])
              for period in periods}
    cuts = list(records('CN Credit Portfolio Snapshot', filters={
        'disabled': 0, 'docstatus': ['!=', 2],
        'status': ['in', ['Importado', 'Importado con alertas']],
        'report_date': ['<=', max(months.values())],
    }, fields=['name', 'report_date'], order_by='report_date desc, name desc'))
    by_period = {name: next((cut['name'] for cut in cuts
                            if getdate(cut['report_date']) <= month), None)
                 for name, month in months.items()}
    loans_by_cut = defaultdict(set)
    for row in details:
        if by_period[row['name']]:
            loans_by_cut[by_period[row['name']]].add(row['loan_number'])
    by_credit = defaultdict(list)
    for cut, loans in loans_by_cut.items():
        for credit in get_credit_rows('CN Credit Portfolio Row', filters={
            'parent': cut, 'parenttype': 'CN Credit Portfolio Snapshot', 'parentfield': 'rows',
        }, fields=['credit_number', 'credit_status', 'employer'], loan_field='credit_number', loans=loans):
            by_credit[(cut, canonical_credit_number(credit['credit_number']))].append(credit)
    for row in details:
        cut = by_period[row['name']]
        if not cut:
            continue
        matches = by_credit[(cut, row['loan_number'])]
        if not matches:
            status = 'Crédito no encontrado en corte'
        elif len(matches) != 1:
            status = 'Crédito duplicado en corte'
        elif matches[0].get('employer') != row['employer']:
            status = 'Empresa distinta en corte'
        else:
            status = matches[0].get('credit_status') or 'Sin estado en corte'
        row['portfolio_credit_status'] = status


def load_direct_applications(period_names):
    """Discover dependencies without exposing unreadable imports or their amounts."""
    result = []
    names = sorted(period_names)
    for offset in range(0, len(names), PAGE_SIZE):
        scope = {'historical_period': ['in', names[offset:offset + PAGE_SIZE]],
                 'event_type': 'Aplicacion', 'effective': 1, 'match_status': 'Conciliado'}
        parents = {row.parent for row in records_for_dependencies(scope)}
        for start in range(0, len(parents), PAGE_SIZE):
            filters = {'name': ['in', sorted(parents)[start:start + PAGE_SIZE]]}
            readable = list(records('CN Accounting Import', filters=filters, fields=['name']))
            require_complete_evidence('CN Accounting Import', [row.name for row in readable], filters=filters)
            result.extend(child_records('CN Source Row', [row.name for row in readable],
                'CN Accounting Import', 'rows', filters=scope, fields=[
                    'name', 'parent', 'event_type', 'historical_period', 'client', 'client_number', 'client_name',
                    'national_id', 'loan_number', 'currency', 'amount', 'equivalent_currency',
                    'equivalent_amount', 'manual_fx_rate', 'fx_basis', 'application_adjustment_usd',
                    'historical_remitted_usd', 'historical_detail', 'collection_row_id',
                    'application_allocation_detail']))
    return result


def records_for_dependencies(scope):
    # Internal IDs only; financial reads below require permission to each parent.
    from credinomina_reconciliation.report_records import _pages
    return _pages(frappe.get_all, 'CN Source Row', fields=['parent'],
                  filters={**scope, 'parenttype': 'CN Accounting Import', 'parentfield': 'rows'},
                  order_by='name asc')


def _add(row, field, value):
    row[field] = None if row[field] is None or value is None else money(row[field]) + money(value)


def _cash(row, details, applied, adjustment, expected_cash=None):
    try:
        all_entries = entries(details)
        cash_entries = [entry for entry in all_entries
                        if entry.get('destino') != 'Partida complementaria'
                        and not entry.get('aplicacion_directa')]
        credit = sum_money(entry.get('importe_usd') for entry in cash_entries if entry.get('saldo_a_favor'))
        cash = sum_money(entry.get('importe_usd') for entry in cash_entries if not entry.get('saldo_a_favor'))
        if expected_cash is not None and sum_money(e.get('importe_usd') for e in all_entries) != money(expected_cash):
            raise ValueError('Cobertura sin detalle verificable')
        if applied is None or cash < 0 or credit < 0:
            raise ValueError('Importes no determinables')
        _add(row, 'deposit_usd', cash)
        _add(row, 'credit_used_usd', credit)
        _add(row, 'cash_adjustment_usd', adjustment)
        # Sum pending claims, never net one customer's excess against another debt.
        _add(row, 'open_usd', max(money(applied) + money(adjustment) - cash - credit, 0))
    except (ValueError, TypeError):
        for field in ('deposit_usd', 'credit_used_usd', 'cash_adjustment_usd', 'open_usd'):
            row[field] = None
        row['_notes'].add('Cobertura no determinable: revise el detalle de distribución.')


def _finish(row):
    for field in AMOUNTS:
        row[field] = None if row[field] is None else money_float(row[field])
    row['collection_difference_usd'] = (None if row['collection_usd'] is None or row['application_usd'] is None
        else money_float(money(row['collection_usd']) - money(row['application_usd'])))
    row['deposit_difference_usd'] = (None if row['application_usd'] is None or row['deposit_usd'] is None
        else money_float(money(row['application_usd']) - money(row['deposit_usd'])))
    row['collection_control'] = ('No aplica' if row['_no_collection'] else 'Sin cobranza' if not row['_has_collection']
        else 'Sin base' if row['collection_difference_usd'] is None
        else 'Con diferencias' if row['collection_difference_usd'] else 'Conforme')
    row['cash_control'] = ('Revisar' if row['open_usd'] is None else 'Pendiente' if row['open_usd'] > 0
        else 'Con excedente' if row['deposit_difference_usd'] is not None
        and money(row['deposit_difference_usd']) + money(row['cash_adjustment_usd']) - money(row['credit_used_usd']) < 0
        else 'Cubierta' if row['application_usd'] else 'Sin aplicación')
    row['observations'] = ' · '.join(sorted(row['_notes']))
    row['has_difference'] = bool(row['_notes'] or row['collection_control'] in {'Sin cobranza', 'Sin base', 'Con diferencias'}
        or row['deposit_difference_usd'] or row['cash_control'] in {'Revisar', 'Pendiente', 'Con excedente'})
    return row


def build_views(periods, collections, applications):
    """Collection projections of direct applications must be removed exactly once."""
    period_map = {row['name']: row for row in periods}
    direct = direct_amounts_by_collection(applications)
    groups = {}

    def group(period_name, source):
        period = period_map[period_name]
        client = source.get('client') or source.get('client_number') or source.get('national_id') or ''
        loan = canonical_credit_number(source.get('loan_number'))
        identity = (canonical_identifier(client), loan)
        if not client and not loan:
            identity = ('row', source.get('name') or id(source))
        key = (period_name, *identity)
        if key not in groups:
            no_collection = bool(period.get('no_collection') or period.get('reconciliation_mode') == 'Historica')
            groups[key] = dict(name=period_name, employer=period.get('employer'), payroll_month=period.get('payroll_month'),
                closure_state=period.get('closure_state'), client_number=client,
                client_name=source.get('client_name') or 'Sin identificar', loan_number=loan,
                usd_currency='USD', _no_collection=no_collection, _has_collection=False, _notes=set(),
                _comments={field: [] for field in COMMENTS},
                **{field: money(0) for field in AMOUNTS})
            if no_collection:
                groups[key]['collection_usd'] = None
        return groups[key]

    for source in collections:
        if source['parent'] not in period_map:
            continue
        row = group(source['parent'], source)
        row['_has_collection'] = True
        for field in COMMENTS:
            comment = str(source.get(field) or '').strip()
            if comment and comment not in row['_comments'][field]:
                row['_comments'][field].append(comment)
        _add(row, 'collection_usd', collection_quality(source)['quality_expected_usd'])
        applied = money(source.get('applied_usd')) - direct[source.get('name')]
        if applied < 0:
            row['_notes'].add('Vínculos directos mayores que la aplicación de cobranza; revise la conciliación.')
            applied = None
        _add(row, 'application_usd', applied)
        try:
            projected = sum_money(entry.get('diferencia_usd') for entry in entries(source.get('remittance_detail'))
                                  if entry.get('aplicacion_directa'))
            _cash(row, source.get('remittance_detail'), applied,
                  money(source.get('rounding_adjustment_usd')) - projected, source.get('remitted_usd'))
        except (ValueError, TypeError):
            _cash(row, 'invalid', applied, 0)

    for source in applications:
        if source.get('historical_period') not in period_map:
            continue
        row = group(source['historical_period'], source)
        applied = converted_amount(source, 'USD')
        _add(row, 'application_usd', applied)
        if applied is None:
            row['_notes'].add('Falta conversión de la aplicación a US$.')
        try:
            adjustment = sum_money(entry.get('diferencia_usd') for entry in entries(source.get('historical_detail')))
            _cash(row, source.get('historical_detail'), applied, adjustment, source.get('historical_remitted_usd'))
        except (ValueError, TypeError):
            _cash(row, 'invalid', applied, 0)

    details = [_finish(row) for row in groups.values()]
    by_period = defaultdict(list)
    for row in details:
        row.update({field: '\n'.join(comments) for field, comments in row.pop('_comments').items()})
        by_period[row['name']].append(row)
    summary = []
    for period in periods:
        children = by_period[period['name']]
        no_collection = bool(period.get('no_collection') or period.get('reconciliation_mode') == 'Historica')
        row = dict(period, _no_collection=no_collection, _has_collection=any(c['_has_collection'] for c in children), _notes=set(),
                   **{field: money(0) for field in AMOUNTS})
        if no_collection:
            row['collection_usd'] = None
        for child in children:
            for field in AMOUNTS:
                _add(row, field, child[field])
            row['_notes'].update(child['_notes'])
        row['credit_count'] = len(children)
        row['difference_count'] = sum(child['has_difference'] for child in children)
        if money(row['application_usd']) != money(period.get('applied_usd')):
            row['_notes'].add('El detalle no coincide con el aplicado del período; revise vínculos y actualice la conciliación.')
            if not children:
                for field in ('application_usd', 'deposit_usd', 'credit_used_usd', 'cash_adjustment_usd', 'open_usd'):
                    row[field] = None
        _finish(row)
        row['has_difference'] = row['has_difference'] or bool(row['difference_count'])
        if any(child['collection_control'] in {'Sin base', 'Con diferencias'} for child in children):
            row['collection_control'] = 'Con diferencias' if row['collection_usd'] is not None else 'Sin base'
        if any(child['cash_control'] == 'Con excedente' for child in children) and row['cash_control'] == 'Cubierta':
            row['cash_control'] = 'Con excedente'
        summary.append(row)
    order = {row['name']: i for i, row in enumerate(periods)}
    details.sort(key=lambda row: (order[row['name']], row['client_name'].casefold(), str(row['client_number']), row['loan_number']))
    for row in [*details, *summary]:
        row.pop('_notes')
        row.pop('_no_collection')
        row.pop('_has_collection')
    return summary, details
