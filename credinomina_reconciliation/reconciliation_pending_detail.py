"""Separate pending receipt/item lines; never modify or infer paid applications."""
from collections import defaultdict
import json

import frappe
from frappe.utils import getdate

from credinomina_reconciliation.complementary_balances import FIELDS, load_balances, cached_balance_reads
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.parsers import canonical_credit_number
from credinomina_reconciliation.report_employers import selected_employers
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.unallocated_deposit_position import build as deposit_positions


def _period_context(source, links, selected, company_months, filters):
    employer = source.get('employer')
    if links:
        matches = [selected[name] for name in links if name in selected and selected[name]['employer'] == employer]
        if matches:
            # Several linked periods do not prove which one owns a remainder.
            return matches[0] if len(links) == 1 else {}
        if any(filters.get(field) for field in ('period', 'status', 'reconciliation_mode', 'collection_cycle',
                                                'historical_scope', 'from_month', 'to_month')):
            return None
        return {}
    when = source.get('event_date')
    if not when:
        return None
    month = getdate(when).strftime('%Y-%m')
    if any(filters.get(field) for field in ('period', 'status', 'reconciliation_mode', 'collection_cycle', 'historical_scope')):
        if (employer, month) not in company_months:
            return None
    for field, lower in (('from_month', True), ('to_month', False)):
        if filters.get(field):
            edge = str(filters[field])[:7]
            if (lower and month < edge) or (not lower and month > edge):
                return None
    return {}


def _line(source, context, amount, *, complementary=False, note='', comments=''):
    when = source.get('event_date')
    period_name = context.get('name')
    row = dict(name=period_name, employer=source.get('employer'),
        payroll_month=context.get('payroll_month') or (getdate(when).replace(day=1) if when else None),
        collection_cycle=context.get('collection_cycle') or '', closure_state=context.get('closure_state') or '',
        client_number=source.get('client_number') or '', national_id=source.get('national_id') or '',
        client_name=source.get('client_name') or 'Sin cliente individual',
        loan_number=canonical_credit_number(source.get('loan_number')), usd_currency='USD',
        collection_usd=None, collection_difference_usd=None, collection_control='No aplica',
        application_usd=0.0, deposit_usd=0.0 if complementary else amount,
        deposit_difference_usd=0.0 if complementary else (None if amount is None else -amount),
        cash_adjustment_usd=amount if complementary else 0.0, credit_used_usd=0.0, conversion_usd=0.0,
        open_usd=0.0, cash_control='Revisar: pendiente de aplicar', has_difference=True,
        difference_reason=source.get('difference_reason') or '', comments=comments, first_exception_comment='',
        portfolio_credit_status='Sin período asociado' if not period_name else 'Sin corte de cartera aplicable',
        _pending_receipt=True, source_doctype=source['source_doctype'], source_document=source['source_document'])
    row['observations'] = '; '.join(filter(None, [
        f"{'Partida complementaria' if complementary else 'Depósito'} {source['source_document']}",
        f"Fecha {getdate(when).strftime('%d/%m/%Y')}" if when else '',
        f"Fila {source['source_rows']}" if source.get('source_rows') else '',
        note, source.get('observation'),
        '' if period_name else 'Sin atribución única a un período; se muestra una sola vez, sin repartir el importe.',
        'Importe pendiente independiente; no cubre aplicaciones ni reduce pendientes de otros clientes.',
    ]))
    return row


def build(periods, deposits, details, period_links, items, balances, filters, *, companies=None):
    output, links = [], defaultdict(set)
    selected = {p['name']: p for p in periods}
    company_months = {(p['employer'], str(p['payroll_month'])[:7]) for p in periods}
    if companies is None:
        companies = selected_employers(filters)
    companies = set(companies) or ({p['employer'] for p in periods} if filters.get('period') else set())
    for link in period_links:
        if link.get('period'):
            links[link['parent']].add(link['period'])
    parents = {}
    for deposit in deposits:
        if (deposit.get('docstatus') == 2 or deposit.get('consolidated_deposit')
                or deposit.get('cash_identity_status') in {'Origen vinculado', 'Revisar identidad'}):
            continue
        if deposit.get('docstatus') != 1:
            if not deposit.get('accounting_source_key'):
                continue
            deposit = dict(deposit, allocated_usd=0, justified_surplus_usd=0,
                           unclassified_usd=deposit.get('amount_usd'), allocation_detail='[]')
        parents[deposit['name']] = deposit
        try:
            ledger = json.loads(deposit.get('allocation_detail') or '[]')
            if isinstance(ledger, list):
                links[deposit['name']].update(e['periodo'] for e in ledger if isinstance(e, dict) and e.get('periodo'))
        except (ValueError, TypeError):
            pass  # Cash attribution is marked unknown by deposit_positions.
    children = {r['name']: r for r in details}
    for position in deposit_positions(list(parents.values()), details):
        source = children.get(position.get('source_rows'), {})
        position = dict(position, employer=source.get('employer') or position.get('employer'),
                        difference_reason=source.get('difference_reason'))
        if companies and position['employer'] not in companies:
            continue
        context = _period_context(position, links[position['source_document']], selected, company_months, filters)
        if context is None:
            continue
        comments = '\n'.join(f'{label}: {source[field]}' for field, label in (
            ('comments', 'Comentarios'), ('application_comment', 'Aplicación'), ('difference_notes', 'Diferencia'))
            if str(source.get(field) or '').strip() not in {'', '0'})
        output.append(_line(position, context, position.get('deposit_pending_usd'), comments=comments,
                            note='Origen del core sin confirmar.' if parents[position['source_document']].get('docstatus') != 1 else ''))
    for item in items:
        balance = balances[item['name']]
        pending = balance.get('pending_usd')
        if (item.get('docstatus') == 2 or item.get('origin_deposit') or item.get('registration_exception')
                or (pending is not None and not money(pending))):
            continue
        source = dict(item, event_date=item.get('posting_date'), source_document=item['name'],
                      source_doctype='CN Complementary Item',
                      client_name=item.get('client_name') or item.get('source_client_name'))
        context = _period_context(source, {item['period']} if item.get('period') else set(), selected, company_months, filters)
        if context is None:
            continue
        net = money(item.get('source_debit')) - money(item.get('source_credit'))
        if item.get('accounting_source_key'):
            amount = signed_pending(item, balance)
            sense = 'Débito' if net > 0 else 'Crédito' if net < 0 else 'Sentido contable sin determinar'
        else:
            amount = None if pending is None or money(pending) < 0 or money(balance.get('used_usd')) < 0 else money_float(money(pending) * (-1 if money(item.get('amount_usd')) < 0 else 1))
            sense = 'Partida manual'
        output.append(_line(source, context, amount, complementary=True,
            note='; '.join(filter(None, [sense, item.get('category'), balance.get('financial_status'),
                                       balance.get('amount_issue'), item.get('description')]))))
    return output


@cached_balance_reads
def load(periods, filters):
    if filters.get('period') and not periods:
        return []
    companies = selected_employers(filters)
    if not companies and filters.get('period'):
        companies = sorted({p['employer'] for p in periods})
    scope = {'employer': ['in', companies]} if companies else {}
    deposit_scope = scope
    if companies:
        from credinomina_reconciliation.paying_employers import load_payer_map
        payers = {payer for payer, beneficiaries in load_payer_map().items() if set(companies) & set(beneficiaries)}
        deposit_scope = {'employer': ['in', sorted(set(companies) | payers)]}
    deposits = list(records('CN Remittance Allocation', filters={**deposit_scope, 'docstatus': ['!=', 2]}, fields=[
        'name', 'docstatus', 'employer', 'deposit_date', 'amount_usd', 'allocated_usd', 'justified_surplus_usd',
        'unclassified_usd', 'cash_identity_status', 'consolidated_deposit', 'allocation_detail', 'accounting_source_key',
    ])) if frappe.has_permission('CN Remittance Allocation', 'read') else []
    details = list(child_records('CN Remittance Detail', [d['name'] for d in deposits],
        'CN Remittance Allocation', 'detail_rows', fields=[
            'name', 'parent', 'row_kind', 'employer', 'client_name', 'client_number', 'national_id', 'loan_number',
            'amount_usd', 'client_credit_usd', 'company_credit_usd', 'comments', 'application_comment', 'difference_reason', 'difference_notes']))
    links = list(child_records('CN Remittance Period', [d['name'] for d in deposits],
        'CN Remittance Allocation', 'detail_periods', fields=['parent', 'period']))
    items = list(records('CN Complementary Item', filters={**scope, 'docstatus': ['!=', 2]},
        fields=list(dict.fromkeys([*FIELDS, 'client_number', 'client_name', 'source_client_name', 'loan_number'])))) \
        if frappe.has_permission('CN Complementary Item', 'read') else []
    return build(periods, deposits, details, links, items, load_balances(items), filters, companies=companies)
