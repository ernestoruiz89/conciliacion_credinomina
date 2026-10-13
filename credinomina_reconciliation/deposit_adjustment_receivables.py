"""Receivables transferred from applications by manual negative deposit adjustments.

Being fully distributed (or posted in the core) is not repayment of this debt.
Only actual distributions of confirmed deposits count, never planned targets.
"""
from collections import defaultdict

import frappe
from frappe.utils import getdate

from credinomina_reconciliation.complementary_balances import load_balances
from credinomina_reconciliation.complementary_subcategories import RECEIVABLE
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float


def build_receivables(items, balances, employer=None, year=None, *, settlements=(), settlement_balances=None, include_settled=False):
    output = []
    for item in items:
        if (item.get('docstatus') != 1 or item.get('category') != 'Ajuste de conciliación'
                or item.get('subcategory_effect') != RECEIVABLE
                or item.get('accounting_source_key') or money(item.get('amount_usd')) >= 0
                or item.get('review_action') == 'No conciliatoria'):
            continue
        when = item.get('posting_date')
        if year and (not when or getdate(when).year != int(year)):
            continue
        # A generic item can transfer receivables to several companies. Use the
        # actual destination company, not the company paying a shared deposit.
        grouped = defaultdict(lambda: {'amount': money(0), 'deposits': set(), 'periods': set()})
        for entry in balances[item.get('name')].get('distributions') or []:
            company = entry.get('employer') or item.get('employer') or ''
            if employer and company != employer:
                continue
            group = grouped[company]
            group['amount'] -= money(entry.get('amount_usd'))
            if entry.get('deposit'):
                group['deposits'].add(entry['deposit'])
            if entry.get('period'):
                group['periods'].add(entry['period'])
        for company, group in sorted(grouped.items()):
            if group['amount'] <= 0:
                continue
            paid, compensated = money(0), money(0)
            recoveries = []
            invalid_recovery = False
            for receipt in settlements:
                if (receipt.get('receivable_origin') != item.get('name')
                        or receipt.get('employer') != company or receipt.get('docstatus') != 1):
                    continue
                value = (settlement_balances or {}).get(receipt['name'], {})
                if receipt.get('category') == 'Compensación entre partidas':
                    amount = money(receipt.get('compensated_usd'))
                    compensated += amount
                    kind = 'Compensación'
                else:
                    amount = sum((money(entry.get('amount_usd')) for entry in value.get('distributions') or []), money(0))
                    paid += amount
                    kind = 'Depósito'
                invalid_recovery = invalid_recovery or amount < 0
                recoveries.append(dict(name=receipt['name'], kind=kind, amount_usd=money_float(amount),
                    date=receipt.get('posting_date'), distributions=value.get('distributions') or []))
            remaining = group['amount'] - paid - compensated
            reviewing = remaining < 0 or invalid_recovery
            if not remaining and not include_settled and not reviewing:
                continue
            observation = (f'Revisar cobros: CxC original US$ {group["amount"]:.2f}, '
                f'cobrado US$ {paid:.2f}, compensado US$ {compensated:.2f}. '
                'El saldo queda sin determinar hasta corregir los vínculos; el exceso no es un saldo a favor.') if reviewing else ''
            source = item.as_dict() if callable(getattr(item, 'as_dict', None)) else dict(item)
            output.append(dict(source, employer=company or None,
                receivable_original_usd=money_float(group['amount']),
                receivable_paid_usd=money_float(paid), receivable_compensated_usd=money_float(compensated),
                receivable_usd=None if reviewing else money_float(remaining), recoveries=recoveries,
                observed_usd=money_float(max(-remaining, abs(min(paid, 0)), abs(min(compensated, 0)))) if reviewing else None,
                observation=observation,
                related_deposits=', '.join(sorted(group['deposits'])),
                period=next(iter(group['periods'])) if len(group['periods']) == 1 else item.get('period'),
                receivable_status='Revisar cobros' if reviewing else 'Cobrada' if not remaining else 'Parcialmente cobrada' if paid or compensated else 'Pendiente de cobro'))
    return output


def receivable_total(rows):
    """Unknown recovery evidence makes the total unknown, never a credit."""
    rows = list(rows)
    if any(row.get('receivable_usd') is None for row in rows):
        return None
    return money_float(sum((money(row['receivable_usd']) for row in rows), money(0)))


def load_receivables(employer=None, year=None, item_names=None):
    # Do not filter the owning company/date before reading cross-company uses.
    items = list(records('CN Complementary Item', filters={
        **({'name': ['in', list(item_names)]} if item_names is not None else {}),
        'docstatus': 1, 'category': 'Ajuste de conciliación', 'amount_usd': ['<', 0],
        'subcategory_effect': RECEIVABLE,
        'accounting_source_key': ['is', 'not set'],
    }, fields=['name', 'employer', 'docstatus', 'category', 'accounting_source_key',
        'amount_usd', 'posting_date', 'review_action', 'accounting_status', 'period',
        'client_name', 'client_number', 'loan_number', 'reference', 'voucher', 'description',
        'subcategory', 'subcategory_effect', 'credit_assigned_to', 'credit_commitment_date', 'reason_type',
        'receivable_due_date', 'receivable_due_origin', 'receivable_setup_pending']))
    settlements = load_settlements(items)
    all_items = [*items, *settlements]
    balances = load_balances(all_items)
    values = build_receivables(items, balances, employer, year, settlements=settlements, settlement_balances=balances)
    numbers = sorted({row.get('client_number') for row in values if row.get('client_number')})
    if numbers and frappe.has_permission('CN Client', 'read'):
        clients = {}
        for offset in range(0, len(numbers), 500):
            clients.update({row.client_number: row for row in records('CN Client',
                filters={'client_number': ['in', numbers[offset:offset + 500]]},
                fields=['name', 'client_number', 'client_name', 'employer', 'national_id'])})
        for row in values:
            client = clients.get(row.get('client_number'))
            # Do not attribute a generic cross-company adjustment to a client
            # belonging to another employer, or read an inaccessible identity.
            if client and client.employer == row.get('employer'):
                row.update(client=client.name, client_name=row.get('client_name') or client.client_name,
                           national_id=client.national_id)
    return values


def load_settlements(items, *, include_cancelled=False):
    from credinomina_reconciliation.report_records import require_complete_evidence
    names = sorted({item.get('name') for item in items if item.get('name')})
    rows = []
    for offset in range(0, len(names), 500):
        filters = {
            'receivable_origin': ['in', names[offset:offset + 500]],
            **({} if include_cancelled else {'docstatus': ['!=', 2]})}
        batch = list(records('CN Complementary Item', filters=filters,
            fields=['name', 'receivable_origin', 'employer', 'docstatus', 'category', 'posting_date',
                'amount_usd', 'compensated_usd', 'compensation_pending_usd', 'accounting_status',
                'receivable_method', 'receivable_counterpart']))
        require_complete_evidence('CN Complementary Item', [row.name for row in batch], filters=filters)
        rows.extend(batch)
    return rows
