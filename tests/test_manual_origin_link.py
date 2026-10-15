from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import manual_origin_link as link
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.complementary_balances import financial_balance, reconciliation_state
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.company_statement import build_detail, summarize


def receipt(**changes):
    return frappe._dict(dict(name='D', modified='M', docstatus=1, employer='EMP', bank_account='BANK',
        deposit_date='2026-10-13', deposit_reference='REF', deposit_currency='NIO', deposit_amount=3650,
        amount_usd=100, fx_rate=36.5, accounting_sources=[], **changes))


def item(name='I', **changes):
    values = dict(name=name, modified='M', docstatus=0, employer='EMP', category='Por clasificar',
        review_action='Pendiente de revisión', accounting_classification='Movimiento interno',
        tmov='01', tdoc='01', accounting_source_key=name, source_date='2026-10-13', source_account='1602',
        source_debit=0, source_credit=3650, source_currency='NIO', source_fx_rate=36.6,
        source_voucher='V', source_description='CONVENIO DEPOSITO REF', source_file='/private/files/a.csv',
        source_file_hash='HASH', source_row=1, accounting_reference='REF', amount_usd=99.73)
    return frappe._dict({**values, **changes})


def test_original_currency_total_and_565_lines_do_not_use_rounded_usd():
    doc = receipt()
    items = [item(str(i), source_credit=6.46, source_row=i + 1) for i in range(564)]
    items.append(item('last', source_credit=6.56, source_row=565))
    result, sources = link.inspect_selection(doc, items, 'internal')
    assert result['issues'] == [] and result['total'] == 3650
    assert len(sources) == 565 and doc.fx_rate == 36.5
    assert all(source['source_fx_rate'] == 36.6 and source['tmov'] == '01' for source in sources)


@pytest.mark.parametrize('change', [dict(source_credit=3649.99), dict(source_credit=3650.01),
    dict(source_currency='USD'), dict(employer='OTHER'), dict(accounting_reference='OTHER'),
    dict(source_date='2026-10-30'), dict(source_debit=1), dict(tmov='02', tdoc='12'),
    dict(docstatus=1), dict(related_application='APPLICATION'), dict(registered_deposit='OTHER'),
    dict(origin_deposit='OTHER'), dict(category='Otros ingresos'), dict(source_file_hash=''),
    dict(compensated_usd=1), dict(review_action='No conciliatoria'), dict(source_voucher='')])
def test_differences_stay_for_review(change):
    result, _ = link.inspect_selection(receipt(), [item(**change)], 'internal')
    assert result['issues'] and not result['already_linked']


@pytest.mark.parametrize('deposit_voucher', ['', 'V', 'BANK-VOUCHER'])
def test_internal_movements_keep_individual_vouchers_for_one_bank_deposit(deposit_voucher):
    doc = receipt(deposit_voucher=deposit_voucher)
    origins = [item(source_credit=1800), item('B', source_credit=1850, source_voucher='V2')]
    result, sources = link.inspect_selection(doc, origins, 'internal')
    assert result['issues'] == [] and result['difference'] == 0
    assert [source['source_voucher'] for source in sources] == ['V', 'V2']
    assert doc.deposit_voucher == deposit_voucher
    doc.accounting_sources = sources
    for origin in origins:
        origin.origin_deposit = doc.name
    retry, _ = link.inspect_selection(doc, origins, 'internal')
    assert retry['already_linked'] and retry['issues'] == []


def test_other_existing_origin_needs_review():
    doc = receipt()
    doc.accounting_source_key = 'OTHER'
    assert link.inspect_selection(doc, [item()], 'internal')[0]['issues']


def test_mixed_currencies_are_not_added_as_if_they_were_the_same_money():
    result, _ = link.inspect_selection(receipt(), [item(source_credit=3600),
        item('B', source_credit=50, source_currency='USD')], 'internal')
    assert result['total'] is None and result['difference'] is None
    assert result['totals_by_currency'] == {'NIO': 3600, 'USD': 50} and result['issues']


@pytest.mark.parametrize('docstatus,category,action', [
    (0, 'Por clasificar', 'Pendiente de revisión'),
    (1, link.LINKED_CATEGORY, 'Partida de depósito'),
])
def test_retry_recognizes_only_complete_matching_evidence_and_link(docstatus, category, action):
    doc, origin = receipt(), item(origin_deposit='D', docstatus=docstatus, category=category, review_action=action)
    _, sources = link.inspect_selection(doc, [origin], 'internal')
    doc.accounting_sources = sources
    doc.accounting_source_key = 'I'
    result, _ = link.inspect_selection(doc, [origin], 'internal')
    assert result['already_linked'] and not result['issues']
    origin.source_credit = 3649
    assert not link.inspect_selection(doc, [origin], 'internal')[0]['already_linked']


def test_later_core_posting_and_upload_do_not_change_bank_date_or_distribution():
    doc = receipt()
    origin = frappe._dict({**doc, **item(), 'name': 'CORE', 'docstatus': 0, 'modified': '2026-10-30',
        'source_date': '2026-10-30', 'accounting_classification': 'Depósito', 'tmov': '02', 'tdoc': '12'})
    result, sources = link.inspect_selection(doc, [origin], 'deposit')
    assert result['issues'] == []
    assert sources[0]['source_date'] == '2026-10-30'
    origin.docstatus = 1
    assert link.inspect_selection(doc, [origin], 'deposit')[0]['issues']
    origin.docstatus = 0
    origin.targets = [dict(period='P')]
    assert link.inspect_selection(doc, [origin], 'deposit')[0]['issues']


@pytest.mark.parametrize('docstatus', [0, 1])
def test_linked_internal_has_no_second_financial_balance_or_ledger_turnover(docstatus):
    doc, origin = receipt(), item(origin_deposit='D', docstatus=docstatus,
        category=link.LINKED_CATEGORY, review_action='Partida de depósito')
    _, doc.accounting_sources = link.inspect_selection(doc, [origin], 'internal')
    doc.accounting_source_key = 'I'
    doc.allocated_usd = 100
    balance = financial_balance(origin)
    assert balance['pending_usd'] == signed_pending(origin, balance) == 0
    assert balance['used_usd'] == 0  # Evidence is not another cash distribution.
    assert reconciliation_state(balance) == 'Vinculado a depósito'
    from credinomina_reconciliation.follow_up_queue import build_follow_up
    assert not build_follow_up([origin], {origin.name: balance}, [])
    origin.update(_balance=balance, _signed_pending_usd=signed_pending(origin, balance))
    assert not build_detail([], [origin], [], [])
    for mirrors in ([], [dict(origin, name='ROW', event_type='Ajuste', parent='IMP', complementary_item='I')]):
        rows, _ = build_rows(mirrors, {'IMP': dict(name='IMP', employer='EMP')}, [origin], deposits=[doc])
        assert len(rows) == 1 and rows[0]['credit_nio'] == 3650
        assert rows[0]['movement_type'] == 'Movimiento interno'
        assert rows[0]['remittance_allocation'] == 'D' and not rows[0]['warning']


def test_ordinary_saves_cannot_forge_or_repurpose_linked_items():
    for doc, previous in [(item(origin_deposit='D'), None), (item(), item(origin_deposit='D')),
                         (item(docstatus=1, origin_deposit='D'), item(origin_deposit='D')),
                         (item(category=link.LINKED_CATEGORY), None)]:
        with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
            link.guard_item(doc, previous)


def test_origin_submission_uses_normal_submit_without_reconciliation_and_resets_context():
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem
    previous = item()
    origin = item(check_permission=Mock(), get_doc_before_save=lambda: previous, _reconcile=Mock())

    def submit():
        origin.docstatus = 1
        CNComplementaryItem.validate(origin)
        CNComplementaryItem.on_submit(origin)

    origin.submit = Mock(side_effect=submit)
    link._submit_internal_origins(receipt(), [origin], 'Comprobante revisado')
    origin.check_permission.assert_called_once_with('submit')
    origin.submit.assert_called_once()
    origin._reconcile.assert_not_called()
    assert origin.origin_deposit == 'D' and origin.review_status == 'Origen contable vinculado'
    assert origin.category == link.LINKED_CATEGORY
    assert origin.reference == 'REF' and origin.review_action == 'Partida de depósito'
    assert origin.review_notes == 'Comprobante revisado'
    assert link._submitting_origin.get() is None
    link._submit_internal_origins(receipt(), [origin], 'Retry')
    origin.submit.assert_called_once()


def test_submit_permissions_for_all_origins_precede_mutation():
    first = item(check_permission=Mock(), submit=Mock())
    second = item('B', check_permission=Mock(side_effect=PermissionError), submit=Mock())
    with pytest.raises(PermissionError):
        link._submit_internal_origins(receipt(), [first, second], 'Revisado')
    first.submit.assert_not_called()
    assert not first.origin_deposit


def test_failed_submission_cannot_leave_a_trusted_validation_context():
    origin = item(check_permission=Mock(), submit=Mock(side_effect=ValueError))
    with pytest.raises(ValueError):
        link._submit_internal_origins(receipt(), [origin], 'Revisado')
    assert link._submitting_origin.get() is None


def test_confirmed_origin_cannot_be_selected_or_allocated_as_another_payment():
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _allocate_complementary_items
    origin = item(docstatus=1, category=link.LINKED_CATEGORY, origin_deposit='D')
    target = frappe._dict(amount_usd=10, complementary_item=origin.name)
    with patch.object(frappe.db, 'get_value', return_value=origin), \
            patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        CNRemittanceAllocation._validate_target(target)
    period = frappe._dict(collection_rows=[frappe._dict(name='ROW')])
    with patch('credinomina_reconciliation.provisional_adjustments.explicit_complement_rows',
               return_value={origin.name: 'ROW'}):
        assert not _allocate_complementary_items([origin], [period])


def test_stale_preview_and_permission_checks_precede_writes():
    doc, origin = receipt(), item()
    from contextlib import nullcontext
    with patch('credinomina_reconciliation.accounting_batch_store.creation_guard', return_value=nullcontext()), \
            patch.object(link, '_load', return_value=(doc, [origin])), patch.object(link, '_check_ownership'), \
            patch.object(link, '_insert_sources') as insert, patch.object(frappe, 'throw', side_effect=ValueError):
        with pytest.raises(ValueError):
            link.link('D', ['I'], 'stale', 'Comprobante verificado')
        insert.assert_not_called()


def test_duplicate_key_on_another_deposit_is_blocked():
    with patch.object(frappe, 'get_all', side_effect=[[frappe._dict(parent='OTHER')], []]), \
            patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        link._check_ownership(receipt(), [item()], [item()], 'internal')


def test_linked_core_cash_is_counted_once_even_when_manual_parent_is_draft():
    doc = receipt()
    doc.update(docstatus=0, linked_core_deposit='CORE', origin_link_status='Pendiente de revisión')
    archived = frappe._dict(doc, name='CORE', cash_identity_status='Origen vinculado', accounting_source_key='KEY')
    totals = summarize(build_detail([], [], [], [doc, archived]))
    assert len(totals) == 1 and totals[0]['deposit_pending_usd'] == -100


def test_linked_items_cannot_also_compensate_or_verify_another_adjustment():
    from credinomina_reconciliation.complementary_compensation import _eligible
    from credinomina_reconciliation.complementary_exceptions import _check_evidence_item
    linked = item(origin_deposit='D', check_permission=lambda _: None)
    with patch.object(frappe, 'throw', side_effect=ValueError):
        with pytest.raises(ValueError):
            _eligible(linked)
        with pytest.raises(ValueError):
            _check_evidence_item(linked, linked)


def test_core_child_evidence_also_freezes_the_manual_receipt_header():
    from credinomina_reconciliation.accounting_deposits import validate_evidence
    previous = receipt()
    previous.accounting_sources = [item()]
    current = frappe._dict(previous, accounting_source_key='FORGED')
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        validate_evidence(current, previous)
