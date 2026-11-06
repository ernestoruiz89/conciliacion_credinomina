from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import manual_origin_link as api
from credinomina_reconciliation.deposit_origin_eligibility import unused_cash_issues, assert_no_cash_dependencies
from credinomina_reconciliation.deposit_accounting_sources import refresh_linked_origin_status


def core(**changes):
    return frappe._dict({'name': 'CORE', 'docstatus': 1, 'modified': 'M', 'employer': 'EMP', 'bank_account': 'BANK',
        'deposit_reference': 'REF', 'deposit_currency': 'NIO', 'deposit_amount': 3650, 'deposit_date': '2026-10-13',
        'accounting_source_key': 'KEY', 'source_credit': 3650, 'source_debit': 0, 'source_currency': 'NIO',
        'source_fx_rate': 36.6, 'source_file': '/private/files/a.csv', 'source_file_hash': 'HASH',
        'source_row': 1, 'source_account': '1602', 'accounting_reference': 'REF',
        'accounting_classification': 'Depósito', 'tmov': '02', 'tdoc': '12',
        'source_date': '2026-10-30', 'allocated_usd': 0, 'justified_surplus_usd': 0,
        'allocation_detail': '[]', **changes})


@pytest.mark.parametrize('changes', [dict(allocated_usd=.01), dict(justified_surplus_usd=.01),
    dict(credit_coverage_usd=.01), dict(conversion_coverage_usd=.01), dict(detail_count=1),
    dict(detail_rows=[{}]), dict(targets=[{}]), dict(detail_periods=[{}]), dict(detail_file='detail.xlsx'),
    dict(detail_source_file='detail.xlsx'), dict(detail_hash='hash'), dict(detail_origin='Aplicaciones'),
    dict(allocation_detail='[{}]'), dict(allocation_detail='broken'), dict(docstatus=2)])
def test_used_or_planned_core_cannot_become_evidence(changes):
    assert not unused_cash_issues(core())
    assert unused_cash_issues(core(**changes))


def test_confirmed_unused_core_is_accepted_only_with_confirmed_parent():
    parent = core(name='MANUAL', accounting_source_key='', accounting_sources=[])
    assert not api.inspect_selection(parent, [core()], 'deposit')[0]['issues']
    parent.docstatus = 0
    assert api.inspect_selection(parent, [core()], 'deposit')[0]['issues']


def test_unrestricted_dependencies_prevent_linking_hidden_reservations():
    for blocked in ('CN Remittance Target', 'CN Complementary Item', 'CN Reconciliation Period', 'CN Remittance Allocation'):
        with patch.object(frappe, 'db', Mock(exists=Mock(side_effect=lambda dt, *a, **k: dt == blocked))), \
                patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: ['HIDDEN'] if dt == blocked else []), \
                patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
            assert_no_cash_dependencies(core(), 'MANUAL')


def test_linked_confirmed_origin_status_does_not_rewrite_confirmation_or_cash():
    doc = core(consolidated_deposit='MANUAL', cash_identity_status='Origen vinculado', financial_status='Pendiente')
    with patch.object(frappe, 'db', Mock()) as db:
        assert refresh_linked_origin_status(doc)
        assert doc.docstatus == 1 and doc.allocated_usd == 0
        db.set_value.assert_called_once_with('CN Remittance Allocation', 'CORE', 'financial_status',
                                             'Origen contable vinculado', update_modified=False)


def test_selector_includes_unused_confirmed_core_and_excludes_planned_children():
    rows = [core(), core(name='PLANNED')]
    with patch.object(api, '_receipt', return_value=core(name='MANUAL')), \
            patch.object(frappe, 'get_list', return_value=rows) as listing, \
            patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: ['PLANNED'] if dt == 'CN Remittance Target' else []):
        assert [row.name for row in api.candidates('MANUAL', 'deposit')] == ['CORE']
        assert listing.call_args.kwargs['filters']['docstatus'] == ['in', [0, 1]]


def test_cash_lock_precedes_document_locks():
    docs = {'CORE': core(), 'MANUAL': core(name='MANUAL')}
    events = []
    for doc in docs.values():
        doc.check_permission = Mock()
    def get_doc(dt, name, **kw):
        if kw.get('for_update'):
            events.append(name)
        return docs[name]
    with patch.object(frappe, 'get_doc', side_effect=get_doc), \
            patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['EMP']), \
            patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool', side_effect=lambda c: events.append('CASH')):
        api._load('MANUAL', ['CORE'], 'deposit', lock=True)
    assert events == ['CASH', 'CORE', 'MANUAL']


def test_linked_confirmed_origins_are_excluded_from_cash_totals_and_pairs():
    from credinomina_reconciliation.control_deposits import build_cash_deposits
    from credinomina_reconciliation.control_kpis import summarize_deposits
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _registered_deposit_pairs
    evidence = core(amount_usd=319.36, consolidated_deposit='MANUAL', cash_identity_status='Origen vinculado')
    assert not build_cash_deposits([evidence])
    assert summarize_deposits([evidence])['received_usd'] == 0
    assert _registered_deposit_pairs([], [evidence]) == ([], {})
