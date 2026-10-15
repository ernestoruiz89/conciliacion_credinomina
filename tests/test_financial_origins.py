from datetime import date
from decimal import Decimal
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import deposit_accounting_sources as origins, receivable_terms
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.aging import age_balance
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.deposit_identity import DetailIdentityEvidence


def line(key, credit, position):
    return dict(accounting_source_key=key, source_date='2025-04-04', source_row=position,
        source_account='160209013004', source_currency='USD', source_credit=credit,
        source_debit=0, source_voucher='V', source_description='US$100 DEPOSITO POR CONVENIO A',
        source_file_hash='HASH', accounting_reference='REF', tmov='02', tdoc='12', accounting_classification='Depósito')


def test_physical_lines_survive_one_cash_receipt_with_partial_mirror():
    first, second = line('K1', 90, 1), line('K2', 10, 2)
    deposit = dict(first, name='D', employer='A', docstatus=0, amount_usd=100, deposit_date='2025-04-04',
        accounting_sources=[first, second])
    source = dict(first, name='S1', parent='I', event_date='2025-04-04', event_type='Deposito', remittance_allocation='D')
    rows, _ = build_rows([source], {'I': dict(name='I', employer='A')}, [], deposits=[deposit])
    assert len(rows) == 2 and sum(row['credit_usd'] for row in rows) == 100
    assert sorted(row['pending_usd'] for row in rows if row['pending_usd'] is not None) == [100]
    assert summarize(build_detail([], [], [], [deposit]))[0]['deposit_pending_usd'] == -100


def test_uncertain_cash_is_unknown_and_linked_origin_is_not_another_receipt():
    first = dict(line('K1', 90, 1), name='D', employer='A', docstatus=0, amount_usd=100, deposit_date='2025-04-04')
    second = dict(first, name='D2', cash_identity_status='Revisar identidad')
    assert summarize(build_detail([], [], [], [first, second]))[0]['balance_usd'] is None
    second['cash_identity_status'] = 'Origen vinculado'
    assert summarize(build_detail([], [], [], [first, second]))[0]['balance_usd'] == -100


def test_grouping_requires_explicit_same_bank_event_and_does_not_remove_ledger_lines():
    row = dict(line('K1', 90, 1), resolved_employer='A', bank_name_hint='BAC', bank_number_hint='123',
        bank_deposit_currency='USD', bank_deposit_reference='REF', voucher='V', deposit_currency='USD',
        deposit_amount=100, deposit_date='2025-04-04', event_date='2025-04-04')
    key = origins.grouping_key(row, 'HASH')
    assert key == origins.grouping_key(dict(row, source_credit=10, source_row=2, accounting_source_key='K2'), 'HASH')
    for change in (dict(bank_name_hint=''), dict(bank_number_hint='999'), dict(voucher='V2'),
                   dict(resolved_employer='NO IDENTIFICADA'), dict(deposit_amount=200)):
        assert origins.grouping_key(dict(row, **change), 'HASH') != key


def test_origins_cannot_be_forged_removed_or_modified_by_normal_form_saves():
    previous = frappe._dict(accounting_sources=[line('K1', 100, 1)], deposit_group_key='GROUP')
    for document in (frappe._dict(previous, accounting_sources=[]), frappe._dict(previous, deposit_group_key='OTHER'),
                     frappe._dict(previous, accounting_sources=[line('K1', 999, 1)])):
        with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
            origins.validate_sources(document, previous)
    origins.validate_sources(previous, previous)


@pytest.mark.parametrize('stored_number', [float, Decimal])
def test_selecting_detail_period_accepts_browser_serialization_of_ledger_numbers(stored_number):
    from credinomina_reconciliation.accounting_deposits import validate_evidence

    source = dict(line('K1', stored_number('1623.00'), 12324),
        source_date=date(2026, 4, 17), source_currency='NIO',
        source_debit=stored_number('0.00'), source_fx_rate=stored_number('36.624300000'))
    previous = frappe._dict(source, accounting_sources=[source], detail_periods=[])
    browser_source = dict(source, source_date='2026-04-17', source_credit=1623,
        source_debit=0, source_fx_rate=36.6243)
    document = frappe._dict(browser_source, accounting_sources=[browser_source],
        detail_periods=[dict(period='ACEITERA-4-2026-01', applied_usd=43.18)])
    with patch.object(frappe, 'throw', side_effect=ValueError):
        validate_evidence(document, previous)
    assert document.accounting_sources == [browser_source]
    assert previous.accounting_sources == [source]


@pytest.mark.parametrize('field,before,after', [
    ('source_debit', 1581.36, '1581.360000000'),
    ('source_credit', Decimal('1623.00'), 1623),
    ('source_fx_rate', Decimal('36.000000000'), 36),
    ('source_row', 12324, '12324.0'),
    ('source_debit', None, 0),
])
def test_ledger_numeric_representations_preserve_the_same_evidence(field, before, after):
    source = dict(line('K1', 1623, 12324), **{field: before})
    previous = frappe._dict(accounting_sources=[source])
    document = frappe._dict(accounting_sources=[dict(source, **{field: after})])
    with patch.object(frappe, 'throw', side_effect=ValueError):
        origins.validate_sources(document, previous)


@pytest.mark.parametrize('field,value', [
    ('source_debit', '0.000000001'), ('source_credit', '1623.000000001'),
    ('source_fx_rate', '36.624300001'), ('source_row', 12325),
    ('source_date', '2026-04-18'), ('source_voucher', '001013798'),
    ('tmov', '2'), ('tdoc', '12.0'), ('source_description', 'Alterada'),
    ('accounting_source_key', 'OTHER'), ('source_file_hash', 'OTHER'),
    ('origin_complementary_item', 'OTHER'),
])
def test_actual_ledger_changes_still_rejected_without_rounding_or_coercing_text(field, value):
    source = dict(line('K1', 1623, 12324), source_date='2026-04-17',
        source_fx_rate=36.6243, source_voucher='001013797')
    previous = frappe._dict(accounting_sources=[source])
    document = frappe._dict(accounting_sources=[dict(source, **{field: value})])
    with patch.object(frappe, 'throw', side_effect=ValueError) as throw, pytest.raises(ValueError):
        origins.validate_sources(document, previous)
    assert f'Línea 1 de accounting_sources — campos modificados: {field}.' in throw.call_args.args[0]


def test_ledger_error_lists_only_changed_fields_on_each_affected_line():
    sources = [line('K1', 100, 12324), line('K2', 200, 12325), line('K3', 300, 12326)]
    previous = frappe._dict(accounting_sources=sources)
    document = frappe._dict(accounting_sources=[
        dict(sources[0], source_credit=101, source_fx_rate=36.5),
        dict(sources[1], source_credit=200.0),
        dict(sources[2], source_voucher='<script>alterado</script>'),
    ])
    with patch.object(frappe, 'throw', side_effect=ValueError) as throw, pytest.raises(ValueError):
        origins.validate_sources(document, previous)
    message = throw.call_args.args[0]
    assert 'Línea 1 de accounting_sources — campos modificados: source_credit, source_fx_rate.' in message
    assert 'Línea 2' not in message
    assert 'Línea 3 de accounting_sources — campos modificados: source_voucher.' in message
    assert '<script>' not in message


@pytest.mark.parametrize('before,after,expected', [
    ([], [line('K1', 100, 1)], 'accounting_sources: línea 1 agregada.'),
    ([line('K1', 100, 1)], [], 'accounting_sources: línea 1 eliminada.'),
])
def test_ledger_error_identifies_added_and_removed_lines(before, after, expected):
    previous = frappe._dict(accounting_sources=before)
    document = frappe._dict(accounting_sources=after)
    with patch.object(frappe, 'throw', side_effect=ValueError) as throw, pytest.raises(ValueError):
        origins.validate_sources(document, previous)
    assert expected in throw.call_args.args[0]


def receivable(**values):
    return frappe._dict(category='Ajuste de conciliación', subcategory_effect='CxC a la empresa', docstatus=1,
        credit_assigned_to='Operator', credit_commitment_date='2026-10-31', receivable_due_date='2025-05-10', **values)


def test_financial_deadline_is_not_the_management_commitment_and_is_immutable():
    old = receivable()
    document = frappe._dict(old, credit_commitment_date='2027-01-01')
    receivable_terms.validate_terms(document, old)
    assert document.receivable_due_date == '2025-05-10'
    assert age_balance(10, document.receivable_due_date, date(2026, 10, 12))['age_bucket'] == 'days_over_90'
    for change in (dict(receivable_due_date=None), dict(receivable_due_date='2027-01-01'), dict(credit_assigned_to=None)):
        with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
            receivable_terms.validate_terms(frappe._dict(old, **change), old)


def test_new_receivable_requires_financial_and_management_terms_but_legacy_is_visible():
    with patch.object(receivable_terms, 'inherited_deadline', return_value=(None, '')):
        for missing in ('credit_assigned_to', 'credit_commitment_date', 'receivable_due_date'):
            document = receivable()
            document[missing] = None
            with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
                receivable_terms.validate_terms(document)
        old = frappe._dict(receivable(), receivable_setup_pending=1, receivable_due_date=None, credit_assigned_to=None)
        document = frappe._dict(old)
        receivable_terms.validate_terms(document, old)
        assert document.receivable_setup_pending == 1 and not document.receivable_due_date


def test_historical_customer_credit_uses_verified_company_not_current_master():
    from credinomina_reconciliation import client_credit
    client = frappe._dict(name='C', client_number='1', client_name='Ana', employer='NEW', check_permission=lambda _: None)
    row = frappe._dict(name='R', employer='OLD', client='C', client_number='1', loan_number='123-1', deducted_usd=40, linked_usd=30)
    deposit = frappe._dict(name='D', employer='OLD', docstatus=1, amount_usd=40, allocated_usd=30,
        fx_rate=0, detail_rows=[row], check_permission=lambda _: None)
    document = frappe._dict(name='X', amount_usd=10, credit_assigned_to='Operator', credit_commitment_date='2026-10-31',
        credit_treatment='Pendiente de decisión', registered_deposit='D', credit_client='C', employer='OLD',
        client_number='1', loan_number='123-1', credit_detail_row='R', description='Exceso')
    evidence = DetailIdentityEvidence()
    evidence['123-1'] = {'C'}
    evidence.loan_employers['123-1'] = {'OLD'}
    evidence.company_clients['OLD']['C'] = dict(client, employer='OLD')
    with patch.object(client_credit, 'lock_credit_deposit', return_value=deposit), \
            patch.object(client_credit, 'other_reservations', return_value=[]), \
            patch('credinomina_reconciliation.paying_employers.allowed_employers', return_value={'OLD'}), \
            patch('credinomina_reconciliation.client_registry.load_client_index', side_effect=AssertionError('No cargue todos los clientes')), \
            patch('credinomina_reconciliation.deposit_identity.load_detail_loan_clients', return_value=evidence), \
            patch('credinomina_reconciliation.company_credit.ensure_related_periods_open'), \
            patch.object(frappe, 'get_doc', return_value=client):
        client_credit.validate_client_credit(document)
    assert document.employer == 'OLD' and client.employer == 'NEW'


def test_bulk_preview_distinguishes_cash_receipts_from_physical_ledger_lines():
    from credinomina_reconciliation.bulk_accounting_import import _summary
    row = dict(line('K1', 90, 1), resolved_employer='A', bank_name_hint='BAC', bank_number_hint='123',
        bank_deposit_currency='USD', bank_deposit_reference='REF', voucher='V', deposit_currency='USD',
        deposit_amount=100, deposit_date='2025-04-04', event_date='2025-04-04')
    plan = dict(file_hash='HASH', groups=[], deposits=[row, dict(row, source_credit=10, source_row=2,
        accounting_source_key='K2')], complementary=[], issues=[], duplicates=[], excluded=[], already_imported=[])
    preview = _summary(plan)
    assert preview['deposit_count'] == 1 and preview['deposit_source_count'] == 2
    assert preview['deposits'][0]['accounting_line_count'] == 2
    assert preview['sections']['identified']['deposit_count'] == 1
    assert preview['sections']['identified']['deposit_source_count'] == 2


def test_financial_due_includes_secondary_collection_links_without_double_counting():
    rows = [frappe._dict(payment_due_date='2025-05-10', payment_term_origin='Convenio', historical_period='',
        collection_period='OTHER', collection_row_id='OTHER-ROW',
        application_allocation_detail='[{"collection_row_id":"OUR-ROW","amount_usd":10}]'),
        frappe._dict(payment_due_date='2025-06-10', payment_term_origin='Convenio', historical_period='P',
        collection_period='P', collection_row_id='OUR-ROW', application_allocation_detail='[]')]
    with patch.object(frappe, 'has_permission', return_value=True), \
            patch.object(frappe, 'get_list', return_value=['I']), \
            patch.object(frappe.db, 'count', return_value=1), \
            patch.object(frappe, 'get_all', return_value=['OUR-ROW']), \
            patch('credinomina_reconciliation.report_records.child_records', return_value=rows):
        due, origin = receivable_terms.inherited_deadline({'period': 'P', 'employer': 'A'})
        assert due == date(2025, 5, 10) and 'Convenio' in origin
        rows[0].payment_due_date = None
        assert receivable_terms.inherited_deadline({'period': 'P', 'employer': 'A'})[0] is None


def test_beneficiary_search_does_not_stop_before_a_later_eligible_client():
    from inspect import unwrap
    from credinomina_reconciliation.client_credit import search_beneficiaries
    visible = [frappe._dict(name=str(index), client_name='Ana', client_number=str(index), employer='NEW')
        for index in range(501)]
    def clients_batch(*args, **kwargs):
        start = kwargs['limit_start']
        return visible[start:start + kwargs['limit_page_length']]
    def choose(row, *_args):
        return (dict(name='500', employer='OLD'), '') if row['client_number'] == '500' else (None, 'Otra empresa')
    deposit = frappe._dict(employer='OLD', check_permission=lambda _: None)
    with patch.object(frappe, 'get_doc', return_value=deposit), \
            patch.object(frappe, 'get_list', side_effect=clients_batch), \
            patch('credinomina_reconciliation.paying_employers.allowed_employers', return_value={'OLD'}), \
            patch('credinomina_reconciliation.paying_employers.choose_detail_client', side_effect=choose), \
            patch('credinomina_reconciliation.deposit_identity.load_detail_loan_clients', return_value={}):
        found = unwrap(search_beneficiaries)('CN Client', 'Ana', 'name', 0, 20,
            {'registered_deposit': 'D', 'employer': 'OLD'})
    assert found == [['500', 'Ana', '500', 'OLD']]
