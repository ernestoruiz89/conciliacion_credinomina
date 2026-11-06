from copy import deepcopy
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.deposit_accounting_sources import refresh_linked_origin_status
from credinomina_reconciliation.deposit_presentation import reconciliation_status
from credinomina_reconciliation.patches.v1_0 import refresh_linked_deposit_origin_status as migration


def origin(**changes):
    return frappe._dict(dict(name='CORE', docstatus=0, consolidated_deposit='MANUAL',
        cash_identity_status='Origen vinculado', financial_status='Sin conciliar', result='Pendiente',
        amount_usd=640.18, allocated_usd=0, unclassified_usd=640.18, unallocated_usd=640.18,
        allocation_detail='[]', deposit_currency='NIO', deposit_amount=23445.98, fx_rate=36.6243,
        modified='2026-11-06 09:05:15', modified_by='reviewer@example.test') | changes)


def test_existing_link_refreshes_only_filterable_status_and_is_idempotent():
    doc = origin()
    before = deepcopy(doc)
    with patch.object(frappe, 'db', Mock()) as db:
        assert refresh_linked_origin_status(doc)
        db.set_value.assert_called_once_with('CN Remittance Allocation', 'CORE', 'financial_status',
                                             'Origen contable vinculado', update_modified=False)
        assert not refresh_linked_origin_status(doc)
        assert db.set_value.call_count == 1
    before.financial_status = 'Origen contable vinculado'
    assert doc == before  # Amounts, result, dates, users and execution evidence stay intact.


def test_consolidated_pointer_alone_identifies_origin_without_conciliating_cash():
    doc = origin(cash_identity_status='')
    assert reconciliation_status(doc) == 'Origen contable vinculado'
    assert reconciliation_status(origin(consolidated_deposit='', cash_identity_status='')) == 'Sin conciliar'


def test_ordinary_and_cancelled_deposits_are_not_rewritten():
    with patch.object(frappe, 'db', Mock()) as db:
        for changes in (dict(docstatus=2),
                        dict(consolidated_deposit='', cash_identity_status='')):
            assert not refresh_linked_origin_status(origin(**changes))
        db.set_value.assert_not_called()


def test_patch_changes_only_linked_status_preserves_money_and_skips_repeat():
    docs = [origin(), origin(name='SAME', financial_status='Origen contable vinculado')]
    with patch.object(migration, '_pages', return_value=docs) as read, patch.object(frappe, 'db', Mock()) as db:
        migration.execute()
        migration.execute()
        db.set_value.assert_called_once_with('CN Remittance Allocation', 'CORE', 'financial_status',
                                             'Origen contable vinculado', update_modified=False)
        assert read.call_args.kwargs['filters'] == {'docstatus': ['in', [0, 1]]}
        assert read.call_args.kwargs['or_filters'] == [
            ['cash_identity_status', '=', 'Origen vinculado'], ['consolidated_deposit', 'is', 'set']]
