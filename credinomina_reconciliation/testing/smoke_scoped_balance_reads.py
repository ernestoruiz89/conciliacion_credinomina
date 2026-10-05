"""Real SQL: a form reads only journals using its items, without losing cash.

Restricted to the isolated test site. Synthetic records always roll back.
"""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_balances import (
    _distribution_index, cached_balance_reads, load_balances,
)
from credinomina_reconciliation.report_records import records
from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        marker = 'SCOPED-READ-' + frappe.generate_hash(length=8)
        _, _, _, first, first_deposit = fixture(marker + '-A')
        _, _, _, second, second_deposit = fixture(marker + '-B')
        broad = _distribution_index()
        assert broad[first.name][0]['deposit'] == first_deposit.name
        assert broad[second.name][0]['deposit'] == second_deposit.name
        calls = []

        def tracked(doctype, **kwargs):
            rows = list(records(doctype, **kwargs))
            if doctype == 'CN Remittance Allocation':
                calls.append(dict(names=[row.name for row in rows], query=kwargs))
            return rows

        @cached_balance_reads
        def read():
            a = load_balances([first])[first.name]
            repeated = load_balances([first])[first.name]
            b = load_balances([second])[second.name]
            return a, repeated, b

        with patch('credinomina_reconciliation.report_records.records', side_effect=tracked):
            a, repeated, b = read()
        assert len(calls) == 2
        assert calls[0]['names'] == [first_deposit.name], calls
        assert calls[1]['names'] == [second_deposit.name], calls
        assert a == repeated and a['distributions'] == broad[first.name]
        assert b['distributions'] == broad[second.name]
        assert a['used_usd'] == b['used_usd'] == 10
        assert all('employer' not in call['query']['filters'] for call in calls)
        return dict(scoped_sql=True, unrelated_deposits_not_loaded=True,
                    cached_repeated_read=True, same_signed_distributions=True,
                    company_and_date_unrestricted=True, rolled_back=True)
    finally:
        frappe.db.rollback()
