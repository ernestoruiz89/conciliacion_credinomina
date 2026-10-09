"""Read-only follow-up for deposited payroll that the core has not applied.

These are evidence links, never allocations or documented customer credits.
The caller supplies permission-filtered deposit and period parents.
"""
from collections import defaultdict

import frappe

from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.deposit_reconciliation import entries
from credinomina_reconciliation.parsers import canonical_credit_number
from credinomina_reconciliation.remittance_detail import _candidate_matches
from credinomina_reconciliation.remittance_periods import selected_periods
from credinomina_reconciliation.rounding import money, money_float


def build_pending_payments(periods, deposits, collections, details, target_links=()):
    periods = {period['name']: period for period in periods
               if period.get('reconciliation_mode') == 'Operativa'}
    candidates = defaultdict(list)
    for row in collections:
        period = periods.get(row.get('parent'))
        if period:
            key = (period['employer'], canonical_credit_number(row.get('loan_number')))
            candidates[key].append(dict(row, group=period['employer'], kind='C'))
    links = defaultdict(set)
    for target in target_links:
        links[target['parent']].add(target['period'])
    detail_by_deposit = defaultdict(list)
    for row in details:
        detail_by_deposit[row['parent']].append(row)
    tasks, used = [], defaultdict(lambda: money(0))
    for deposit in deposits:
        available = money(deposit.get('unclassified_usd'))
        if (available <= 0 or deposit.get('result') == 'Revisar destinos'
                or deposit.get('detail_status') in {'Detalle supera depósito', 'Importar detalle actualizado'}
                or any(money(row.get('pending_usd')) < 0 for row in detail_by_deposit[deposit['name']])):
            continue
        scope = set(selected_periods(deposit)) | links[deposit['name']]
        scope.update(entry.get('periodo') for entry in entries(deposit.get('allocation_detail'))
                     if entry.get('periodo'))
        for row in detail_by_deposit[deposit['name']]:
            pending = money(row.get('pending_usd'))
            if pending <= 0 or available <= 0:
                continue
            # A loan identifier is required: a name alone cannot prove who is
            # waiting for application. Conflicting client identifiers still fail.
            if not row.get('loan_number'):
                continue
            employer = row.get('employer') or deposit.get('employer')
            key = (employer, canonical_credit_number(row['loan_number']))
            matches = [candidate for candidate in candidates[key]
                       if (not scope or candidate['parent'] in scope)
                       and _candidate_matches(row, candidate)]
            if len(matches) != 1:
                continue
            collection = matches[0]
            period = periods[collection['parent']]
            quality = collection_quality(collection, period.get('application_basis'))
            if quality['quality_status'] != 'Aplicación insuficiente':
                continue
            gap = -money(quality['quality_difference_usd']) - used[collection['name']]
            amount = min(pending, gap, available)
            if amount <= 0:
                continue
            used[collection['name']] += amount
            available -= amount
            tasks.append(dict(
                priority=1, kind='pending_application', employer=employer,
                employer_name=period.get('employer_name') or employer,
                period=period['name'], period_label=period['name'],
                summary='Pago pendiente de aplicar · crédito ' + collection.get('loan_number', ''),
                next_action='Revisar o corregir la aplicación en el core, importar los movimientos y volver a conciliar. '
                            'El depósito tiene cobranza identificada; este importe no es un saldo a favor.',
                amount_usd=money_float(amount), due_date=None, responsible='',
                client_name=collection.get('client_name') or row.get('client_name') or '',
                client_number=collection.get('client_number') or '',
                loan_number=collection.get('loan_number') or '',
                deposit_detail_row=row.get('name'), collection_row=collection['name'],
                target_doctype='CN Remittance Allocation', target_name=deposit['name'],
                action_label='Revisar pago pendiente',
            ))
    return tasks


def load_pending_payments(periods, deposits, target_links=()):
    deposits = [deposit for deposit in deposits if money(deposit.get('unclassified_usd')) > 0]
    periods = [period for period in periods if period.get('reconciliation_mode') == 'Operativa']
    if not deposits or not periods:
        return []
    collections, details = [], []
    for parents, doctype, fields, output in (
        (periods, 'CN Collection Row', ['name', 'parent', 'client', 'client_number', 'employee_number',
         'national_id', 'client_name', 'loan_number', 'installment_number', 'expected_usd', 'expected_nio',
         'deducted_usd', 'deducted_nio', 'deduction_status', 'applied_usd', 'complementary_usd'], collections),
        (deposits, 'CN Remittance Detail', ['name', 'parent', 'employer', 'client', 'client_number',
         'employee_number', 'national_id', 'client_name', 'loan_number', 'installment_number',
         'application_reference', 'pending_usd'], details),
    ):
        for offset in range(0, len(parents), 500):
            output.extend(frappe.get_all(doctype, filters={
                'parent': ['in', [parent['name'] for parent in parents[offset:offset + 500]]],
                'parenttype': 'CN Reconciliation Period' if doctype == 'CN Collection Row' else 'CN Remittance Allocation',
            }, fields=fields, order_by='parent asc, idx asc', limit_page_length=0))
    return build_pending_payments(periods, deposits, collections, details, target_links)
