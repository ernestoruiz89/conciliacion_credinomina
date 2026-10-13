"""Regression checks for the operational review: classification is not settlement."""
from credinomina_reconciliation.complementary_balances import financial_balance, reconciliation_state
from credinomina_reconciliation.complementary_transactions import progress
from credinomina_reconciliation.accounting_control import _status
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.follow_up_queue import group_work_cases, filter_work


def test_non_conciliatory_keeps_original_ledger_balance():
    item = dict(name='C', category='Por clasificar', docstatus=0, amount_usd=100,
                review_action='No conciliatoria', review_status='No conciliatoria',
                source_debit=0, source_credit=100)
    balance = financial_balance(item)
    assert balance['pending_usd'] == 100
    assert signed_pending(item, balance) == -100
    assert progress(item, balance)['state'] == 'Pendiente'
    assert _status({}, dict(item, _financial_balance=balance)) == 'Pendiente'


def test_all_reports_reject_overassignment_and_wrong_direction():
    for amount, assigned in [(100, 110), (-100, -110), (100, -100), (-100, 100)]:
        item = dict(name='C', category='Otros ingresos', docstatus=1, amount_usd=amount,
                    cash_assigned_usd=assigned)
        balance = financial_balance(item, [dict(amount_usd=assigned)])
        assert reconciliation_state(balance) == 'Revisar distribución'
        assert _status({}, item) == 'Revisar distribución'
        assert progress(item, balance)['state'] == 'Revisar distribución'
        assert progress(item, balance)['resolved_usd'] is None


def test_queue_keeps_principal_amount_without_double_counting_accounting():
    common = dict(target_doctype='CN Complementary Item', target_name='C', priority=1, client_name='Ana Pérez', event_date='2025-01-01', reason_type='Por cancelación')
    tasks = [dict(common, kind='accounting_registration', amount_usd=100),
             dict(common, kind='credit_management', amount_usd=40)]
    groups = group_work_cases(tasks)
    assert groups[0]['amount_usd'] == 40
    assert groups[0]['action_count'] == 2
    assert len(filter_work(groups, search='ana', min_amount=30, sort_by='amount')) == 1
    assert filter_work(groups, min_amount=50) == []
    assert filter_work(groups, kind='accounting')[0]['amount_usd'] == 100
    assert len(filter_work(groups, search='cancelación')) == 1


def test_statement_net_zero_still_has_pending_items_and_partial_application():
    application = dict(employer='E', amount_usd=40, applied_usd=100, paid_usd=60,
                       source_import='I', source_rows='R', client_name='Ana', application_date='2025-01-01')
    deposit = dict(name='D', employer='E', docstatus=1, amount_usd=40, result='Pendiente')
    detail = build_detail([application], [], [], [deposit])
    assert next(row for row in detail if row['position_type'] == 'Aplicación')['status'] == 'Parcial'
    summary = summarize(detail)[0]
    assert summary['balance_usd'] == 0
    assert summary['pending_count'] == 2
    assert len(build_detail([application], [], [], [deposit], dict(component='application_pending_usd', search='Ana'))) == 1
