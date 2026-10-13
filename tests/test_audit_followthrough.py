"""Regressions for the October review; classification must not hide obligations."""
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation.imported_complement_amount import ledger_magnitude, validate_imported_magnitude
from credinomina_reconciliation.complementary_balances import financial_balance, get_list_balances
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.follow_up_queue import build_follow_up, group_work_cases, filter_work, load_follow_up
from credinomina_reconciliation.application_adjustments import application_link_error
from credinomina_reconciliation.application_context import linked_application_employer


def core(**values):
    return frappe._dict(dict(name='C', docstatus=0, category='Por clasificar',
        accounting_source_key='key', source_currency='USD', source_debit=100,
        source_credit=0, amount_usd=100, amount=100, currency='USD') | values)


@pytest.mark.parametrize('amount', [50, 101, 0, -50])
def test_imported_magnitude_cannot_shrink_or_grow(amount):
    with patch.object(frappe, 'throw', side_effect=ValueError):
        with pytest.raises(ValueError):
            validate_imported_magnitude(core(amount_usd=amount), core())


def test_sign_and_currency_equivalence_are_allowed_but_not_new_fx_magnitude():
    validate_imported_magnitude(core(amount_usd=-100), core())
    item = core(source_currency='NIO', source_credit=3662.43, source_debit=0, source_fx_rate=36.6243)
    assert ledger_magnitude(item) == 100
    validate_imported_magnitude(item)
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        validate_imported_magnitude(dict(item, amount_usd=99))
    # Manual records are not bound to an imported ledger.
    validate_imported_magnitude(dict(item, accounting_source_key='', amount_usd=1))


def test_legacy_missing_rate_preserves_saved_magnitude():
    old = core(source_currency='NIO', source_fx_rate=0)
    validate_imported_magnitude(dict(old, amount_usd=-100), old)
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        validate_imported_magnitude(dict(old, amount_usd=50), old)


def test_existing_amount_discrepancy_visible_without_rewriting_evidence():
    item = core(amount_usd=50)
    result = financial_balance(item)
    assert result['pending_usd'] == 100
    assert result['financial_status'] == 'Revisar distribución'
    assert result['amount_issue']
    assert signed_pending(item, result) == 100
    assert item.amount_usd == 50


def test_partial_distribution_keeps_original_remainder():
    item = core(category='Otros ingresos', docstatus=1)
    result = financial_balance(item, [dict(amount_usd=50)])
    assert result['used_usd'] == result['pending_usd'] == 50


def test_controller_checks_magnitude_after_currency_conversion():
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem
    old = core(doctype='CN Complementary Item', employer='A', review_action='Pendiente de revisión',
        reference='', voucher='', voucher_line='', flags=frappe._dict(), compensations=[])
    new = frappe._dict(old, amount=50)
    new.get_doc_before_save = lambda: old
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        CNComplementaryItem.validate(new)


def test_import_picker_search_includes_legacy_and_checks_read_permissions():
    from credinomina_reconciliation.accounting_review import application_import_query
    from unittest.mock import Mock
    company = Mock()
    with patch.object(frappe, 'get_doc', return_value=company), \
            patch.object(frappe, 'get_list', return_value=[frappe._dict(name='I', employer='')]) as query:
        # Exercise the query below Frappe's input-sanitizing wrapper.
        from inspect import unwrap
        result = unwrap(application_import_query)('CN Accounting Import', 'CONTA', 'name', 0, 20, {'employer': 'A'})
    company.check_permission.assert_called_once_with('read')
    assert query.call_args.kwargs['filters']['docstatus'] == ['!=', 2]
    assert query.call_args.kwargs['or_filters'] == [['employer', '=', 'A'], ['employer', 'is', 'not set']]
    assert result[0][0] == 'I'


def test_shared_receivables_are_independent_cases_and_filterable():
    rows = [dict(name='C', employer=company, receivable_usd=amount)
            for company, amount in [('A', 200), ('B', 300)]]
    tasks = build_follow_up([], {}, [], adjustment_receivables=rows)
    grouped = group_work_cases(tasks)
    assert [(row['employer'], row['amount_usd']) for row in grouped] == [('A', 200), ('B', 300)]
    assert filter_work(grouped, min_amount=250)[0]['employer'] == 'B'
    assert len(group_work_cases([*tasks, dict(tasks[0], kind='accounting_registration')])) == 2


def test_list_shows_receivable_despite_fully_distributed_adjustment():
    item = dict(name='C', docstatus=1, category='Ajuste de conciliación',
        subcategory_effect='CxC a la empresa', amount_usd=-500)
    value = financial_balance(item, [dict(amount_usd=-500)])
    with patch.object(frappe, 'has_permission', return_value=True), \
            patch('credinomina_reconciliation.report_records.records', return_value=[item]), \
            patch('credinomina_reconciliation.complementary_balances.load_balances', return_value={'C': value}), \
            patch('credinomina_reconciliation.deposit_adjustment_receivables.load_receivables',
                return_value=[dict(name='C', receivable_usd=200), dict(name='C', receivable_usd=300)]) as read:
        result = get_list_balances(['C'])['C']
    assert result['state'] == 'Conciliado'
    assert result['pending_usd'] == 0
    assert result['company_receivable_usd'] == 500
    read.assert_called_once_with(item_names=['C'])


def test_registration_exception_year_follows_origin_not_commitment():
    item = dict(name='C', posting_date='2025-12-01', category='Otros ingresos',
        amount_usd=10, docstatus=1, accounting_exception='EX', accounting_status='Pendiente de registro')
    exception = dict(name='EX', complementary_item='C', creation='2026-01-01',
        commitment_date='2027-01-01', amount_usd=10, status='Abierta')
    def query(dt, **kwargs):
        if dt == 'CN Reconciliation Exception':
            return [dict(exception)]
        if 'name' in kwargs['filters']:
            return [item]
        return [] if '2026' in str(kwargs['filters']) else [item]
    with patch.object(frappe, 'has_permission', side_effect=lambda dt, *_: dt != 'CN Remittance Allocation'), \
            patch.object(frappe, 'get_list', side_effect=query), \
            patch('credinomina_reconciliation.follow_up_queue.load_balances', return_value={'C': financial_balance(item, [dict(amount_usd=10)])}), \
            patch('credinomina_reconciliation.follow_up_queue.load_credit_periods', return_value={}), \
            patch('frappe.utils.nowdate', return_value='2026-10-12'):
        tasks = load_follow_up(2025)
        assert tasks[0]['target_name'] == 'EX'
        assert tasks[0]['event_date'] == '2025-12-01'
        assert tasks[0]['due_date'] == '2027-01-01'
        assert load_follow_up(2026) == []


def test_legacy_application_link_resolves_company_without_changing_header():
    row = frappe._dict(parenttype='CN Accounting Import', event_type='Aplicacion', effective=1,
        currency='USD', historical_period='P')
    parent = frappe._dict(employer='', docstatus=0, status='Importado')
    item = frappe._dict(employer='A')
    with patch('credinomina_reconciliation.application_context.records', return_value=[dict(name='P', employer='A')]):
        assert application_link_error(item, row, parent) is None
        assert application_link_error(frappe._dict(employer='B'), row, parent)
    with patch('credinomina_reconciliation.application_context.records', return_value=[]):
        assert application_link_error(item, row, parent)
    assert parent.employer == ''


def test_legacy_application_uses_collection_period_and_readable_portfolio():
    parent = frappe._dict(employer='')
    with patch.object(frappe.db, 'get_value', return_value='P'), \
            patch('credinomina_reconciliation.application_context.records', return_value=[dict(name='P', employer='A')]):
        assert linked_application_employer(dict(collection_row_id='ROW'), parent) == 'A'
    with patch('credinomina_reconciliation.application_context.records', return_value=[]):
        assert linked_application_employer(dict(portfolio_employer='PRIVATE'), parent) == ''
