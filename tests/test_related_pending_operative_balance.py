from copy import deepcopy
import json
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import accounting_related_records as related
from credinomina_reconciliation import application_context


def application(name='R', **changes):
    return frappe._dict(dict(name=name, parent='I', source_row=21437, event_date='2026-07-15',
        event_type='Aplicacion', effective=1, match_status='Conciliado', client='123', client_number='123',
        client_name='Cliente de prueba', national_id='', loan_number='0013528-1', currency='USD', amount=42.40,
        amount_usd=42.40, net_applied_usd=42.40, historical_period='', deposit_match_status='Sin depósito') | changes)


def read(sources, collections=None, *, selected=None, readable=True):
    parent = frappe._dict(name='I', employer='IMMSA', status='Importado')
    identity = related._identity_values(sources[0], [sources[0]])
    selected = sources if selected is None else selected
    original = deepcopy((sources, collections))
    with patch.object(frappe, 'get_all', return_value=selected), \
            patch.object(related, 'records', return_value=[parent] if readable else []), \
            patch.object(application_context, 'load_application_context',
                return_value=(sources, {'I':parent}, {}, collections or {}, {})) as context, \
            patch('credinomina_reconciliation.application_deposit_links.load_links', return_value={}), \
            patch('credinomina_reconciliation.application_complementary_links.load_links', return_value={}), \
            patch.object(frappe, 'db', Mock()) as db:
        result = related._application_rows('IMMSA', identity)
        db.set_value.assert_not_called()
        db.sql.assert_not_called()
        assert (sources, collections) == original
        return result, context.call_count


def test_unassigned_operative_rows_have_their_full_pending_amount_even_with_stale_settled_labels():
    for changes in ({}, {'collection_row_id':'C'}, {'deposit_match_status':'Depósito conciliado'}):
        source = application(**changes)
        rows, calls = read([source], {'C':dict(applied_usd=42.40, remittance_detail='[]')})
        assert len(rows) == 1 and rows[0]['pending_usd'] == 42.40
        assert rows[0]['status'] == 'Sin depósito' and calls == 1


def test_accounting_context_uses_the_individual_application_line_and_preserves_missing_core_references():
    sources = [
        application('FIRST', source_row=11, accounting_entry='00021', accounting_source_key='FIRST-KEY',
                    reference='RECIBO', source_description='Primer concepto'),
        application('SECOND', source_row=12, voucher='00022', reference='00032', description='Concepto antiguo'),
    ]
    rows, _ = read(sources)
    assert [row['document'] for row in rows] == ['I', 'I']
    assert [row['core_entry'] for row in rows] == [
        dict(voucher='00021', reference=None, description='Primer concepto'),
        dict(voucher='00022', reference='00032', description='Concepto antiguo'),
    ]


def test_confirmed_application_adjustment_and_multiple_deposits_are_subtracted_once():
    source = application(amount=100, amount_usd=100, application_adjustment_usd=20, net_applied_usd=999,
        collection_row_id='C')
    collection = dict(applied_usd=80, remittance_detail=json.dumps([
        dict(deposito='D1', importe_usd=10), dict(deposito='D2', importe_usd=20)]))
    rows, _ = read([source], {'C':collection})
    assert rows[0]['amount_usd'] == 80 and rows[0]['pending_usd'] == 50
    assert rows[0]['status'] == 'Depósito parcial'


def test_signed_adjustments_and_documented_rounding_keep_their_financial_sign():
    source = application(application_adjustment_usd=-5, collection_row_id='C')
    rows, _ = read([source], {'C':dict(applied_usd=47.40, remittance_detail='[{"importe_usd":20}]',
                                    rounding_adjustment_usd=-.01)})
    assert rows[0]['amount_usd'] == 47.40 and rows[0]['pending_usd'] == 27.39


def test_fully_paid_or_fully_adjusted_rows_are_not_reported_as_pending():
    for source, collection in ((application(collection_row_id='C'), dict(applied_usd=42.40, remittance_detail='[{"importe_usd":42.40}]')),
                               (application(application_adjustment_usd=42.40), {})):
        assert read([source], {'C':collection})[0] == []


def test_exact_fifo_slices_assign_only_each_applications_cash_in_a_shared_collection():
    sources = [application('R', amount=40, collection_row_id='C'), application('R2', amount=60, collection_row_id='C')]
    collection = dict(applied_usd=100, remittance_detail=json.dumps([dict(importe_usd=30,
        aplicaciones_fifo=[dict(application_id='R', amount_usd=10),dict(application_id='R2', amount_usd=20)])]))
    rows, calls = read(sources, {'C':collection})
    assert [row['pending_usd'] for row in rows] == [30,40] and calls == 1


def test_partial_shared_cash_without_slices_stays_unknown_while_fully_paid_shared_cash_covers_each_share():
    sources = [application('R', amount=40, collection_row_id='C'), application('R2', amount=60, collection_row_id='C')]
    for paid, expected in ((0,40), (30,None), (100,'settled')):
        rows, _ = read(sources, {'C':dict(applied_usd=100, remittance_detail=json.dumps([dict(importe_usd=paid)]))},
                       selected=sources[:1])
        if expected == 'settled':
            assert rows == []
        else:
            assert rows[0]['pending_usd'] == expected
            if expected is None:
                assert rows[0]['observations'] and rows[0]['status'] == 'Revisar distribución'


def test_other_applications_are_considered_before_filtering_to_one_clients_rows():
    source = application('R', amount=80, collection_row_id='C')
    peer = application('OTHER', amount=40, collection_row_id='C', client='OTHER', client_number='OTHER')
    rows, _ = read([source,peer], {'C':dict(applied_usd=100, remittance_detail='[{"importe_usd":100}]')}, selected=[source])
    assert rows[0]['pending_usd'] is None and rows[0]['status'] == 'Revisar distribución'


def test_missing_collection_permissions_corrupt_journals_and_missing_conversion_do_not_invent_balances():
    cases = [(application(collection_row_id='HIDDEN'), {}),
             (application(collection_row_id='C'), {'C':dict(applied_usd=42.4, remittance_detail='broken')}),
             (application(currency='NIO', manual_fx_rate=0), {})]
    for source, collections in cases:
        rows, _ = read([source], collections)
        assert len(rows) == 1 and rows[0]['pending_usd'] is None
    assert read([application()], readable=False) == ([],0)


def test_historical_balance_and_query_permissions_keep_the_existing_behavior():
    rows, calls = read([application(historical_period='P', historical_balance_usd=.01)])
    assert rows[0]['pending_usd'] == .01 and calls == 0


def test_unreadable_cash_dependencies_do_not_become_an_assumed_zero_payment():
    with patch.object(application_context, 'load_application_context', side_effect=frappe.PermissionError):
        assert related._operative_progress('IMMSA') == {}


def test_unavailable_progress_keeps_positive_balances_unknown_and_does_not_add_empty_zero_rows():
    with patch.object(related, '_operative_progress', return_value={}):
        rows, _ = read([application()])
        assert rows[0]['pending_usd'] is None and rows[0]['observations']
        assert read([application(amount=0, amount_usd=0, net_applied_usd=0)])[0] == []
