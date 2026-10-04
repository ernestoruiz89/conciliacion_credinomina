"""Receivables transferred from applications by manual negative deposit adjustments.

Being fully distributed (or posted in the core) is not repayment of this debt.
Only actual distributions of confirmed deposits count, never planned targets.
"""
from collections import defaultdict

from frappe.utils import getdate

from credinomina_reconciliation.complementary_balances import load_balances
from credinomina_reconciliation.complementary_subcategories import RECEIVABLE
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float


def build_receivables(items, balances, employer=None, year=None):
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
            source = item.as_dict() if callable(getattr(item, 'as_dict', None)) else dict(item)
            output.append(dict(source, employer=company or None,
                receivable_usd=money_float(group['amount']),
                related_deposits=', '.join(sorted(group['deposits'])),
                period=next(iter(group['periods'])) if len(group['periods']) == 1 else item.get('period'),
                receivable_status='Pendiente de cobro'))
    return output


def load_receivables(employer=None, year=None):
    # Do not filter the owning company/date before reading cross-company uses.
    items = list(records('CN Complementary Item', filters={
        'docstatus': 1, 'category': 'Ajuste de conciliación', 'amount_usd': ['<', 0],
        'subcategory_effect': RECEIVABLE,
        'accounting_source_key': ['is', 'not set'],
    }, fields=['name', 'employer', 'docstatus', 'category', 'accounting_source_key',
        'amount_usd', 'posting_date', 'review_action', 'accounting_status', 'period',
        'client_name', 'client_number', 'loan_number', 'reference', 'voucher', 'description',
        'subcategory', 'subcategory_effect', 'credit_assigned_to', 'credit_commitment_date']))
    return build_receivables(items, load_balances(items), employer, year)
