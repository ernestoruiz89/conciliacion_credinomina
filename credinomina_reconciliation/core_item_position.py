"""Permission-scoped accounting-origin complementary balances, with ledger sign."""
from credinomina_reconciliation.complementary_balances import FIELDS, load_balances
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.rounding import money, money_float


def signed_pending(item, balance):
    """The accounting debit/credit, not the editable amount, determines direction."""
    net = money(item.get('source_debit')) - money(item.get('source_credit'))
    pending = balance.get('pending_usd')
    if pending is None:
        return None
    if not money(pending):
        return 0.0
    if not net:
        return None  # Do not invent a direction for an unidentifiable ledger line.
    return money_float(money(pending) * (1 if net > 0 else -1))


def load_core_items(year=None, employer=None):
    filters = {'docstatus': ['!=', 2], 'accounting_source_key': ['is', 'set']}
    if employer:
        filters['employer'] = employer
    if year:
        filters['source_date'] = ['between', [f'{year}-01-01', f'{year}-12-31']]
    fields = list(dict.fromkeys([*FIELDS, 'accounting_source_key', 'source_date',
        'source_debit', 'source_credit', 'source_currency', 'source_fx_rate',
        'source_voucher', 'source_file', 'source_row', 'source_description',
        'source_client_name', 'client_number', 'loan_number', 'reference', 'voucher']))
    items = list(records('CN Complementary Item', fields=fields, filters=filters))
    balances = load_balances(items)  # All-time uses: date filtering must not reset usage.
    for item in items:
        balance = balances[item['name']]
        item['_balance'] = balance
        item['_signed_pending_usd'] = signed_pending(item, balance)
    return items
