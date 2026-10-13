from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import manual_origin_link as link
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.complementary_balances import financial_balance
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
    dict(compensated_usd=1), dict(review_action='No conciliatoria')])
def test_differences_stay_for_review(change):
    result, _ = link.inspect_selection(receipt(), [item(**change)], 'internal')
    assert result['issues'] and not result['already_linked']


def test_multiple_vouchers_and_other_existing_origin_need_review():
    result, _ = link.inspect_selection(receipt(), [item(source_credit=1800), item('B', source_credit=1850, source_voucher='V2')], 'internal')
    assert any('asientos distintos' in issue for issue in result['issues'])
    doc = receipt()
    doc.accounting_source_key = 'OTHER'
    assert link.inspect_selection(doc, [item()], 'internal')[0]['issues']


def test_mixed_currencies_are_not_added_as_if_they_were_the_same_money():
    result, _ = link.inspect_selection(receipt(), [item(source_credit=3600),
        item('B', source_credit=50, source_currency='USD')], 'internal')
    assert result['total'] is None and result['difference'] is None
    assert result['totals_by_currency'] == {'NIO': 3600, 'USD': 50} and result['issues']


def test_retry_recognizes_only_complete_matching_evidence_and_link():
    doc, origin = receipt(), item(origin_deposit='D')
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


def test_linked_internal_has_no_second_financial_balance_or_ledger_turnover():
    doc, origin = receipt(), item(origin_deposit='D')
    _, doc.accounting_sources = link.inspect_selection(doc, [origin], 'internal')
    doc.accounting_source_key = 'I'
    doc.allocated_usd = 100
    balance = financial_balance(origin)
    assert balance['pending_usd'] == signed_pending(origin, balance) == 0
    assert balance['used_usd'] == 0  # Evidence is not another cash distribution.
    for mirrors in ([], [dict(origin, name='ROW', event_type='Ajuste', parent='IMP', complementary_item='I')]):
        rows, _ = build_rows(mirrors, {'IMP': dict(name='IMP', employer='EMP')}, [origin], deposits=[doc])
        assert len(rows) == 1 and rows[0]['credit_nio'] == 3650
        assert rows[0]['movement_type'] == 'Movimiento interno'
        assert rows[0]['remittance_allocation'] == 'D' and not rows[0]['warning']


def test_ordinary_saves_cannot_forge_or_repurpose_linked_items():
    for doc, previous in [(item(origin_deposit='D'), None), (item(), item(origin_deposit='D'))]:
        with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
            link.guard_item(doc, previous)


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
