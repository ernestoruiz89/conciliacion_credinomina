from copy import deepcopy
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import accounting_difference_helper as helper


class Document(frappe._dict):
    def check_permission(self, permission):
        pass

    def add_comment(self, *args):
        pass


@pytest.fixture
def document():
    return Document(doctype=helper.DOCTYPE, name='IMPORT', modified='v1', docstatus=0,
        status='Importado', rows=[frappe._dict(name=name, doctype='CN Source Row', idx=i,
            event_type='Aplicacion', effective=1, match_status='Conciliado',
            amount_usd=100, historical_remitted_usd=50, historical_balance_usd=50,
            historical_detail='[{"importe_usd":50}]', deposit_match_status=state,
            difference_reason='Anterior' if i == 2 else '', difference_notes='Anterior' if i == 2 else '')
            for i, (name, state) in enumerate([('A', 'Conciliación parcial'), ('B', 'No conciliado')], 1)])


def environment(document):
    stack = ExitStack()
    reason = Mock()
    stack.enter_context(patch.object(frappe, 'get_doc', side_effect=lambda dt, name: reason if dt == 'CN Difference Reason' else document))
    lock = stack.enter_context(patch.object(frappe.db, 'get_value'))
    stack.enter_context(patch.object(frappe.db, 'exists', side_effect=lambda dt, name: name != 'MISSING'))
    write = stack.enter_context(patch.object(frappe.db, 'set_value'))
    stack.enter_context(patch.object(frappe, 'clear_document_cache'))
    return stack, write, lock, reason


def test_load_includes_pending_rows_and_existing_notes_without_writes(document):
    before = deepcopy(document)
    stack, write, _, _ = environment(document)
    with stack:
        result = helper.load('IMPORT')
    assert [row['row_name'] for row in result['rows']] == ['A', 'B']
    assert result['rows'][1]['difference_notes'] == 'Anterior'
    assert document == before
    write.assert_not_called()


@pytest.mark.parametrize('state', ['Conciliado', 'Compensada', 'Conciliada: depósito + ajuste',
                                 'Depósito conciliado', 'Aplicación compensada'])
def test_settled_rows_are_excluded_even_with_previous_documentation(document, state):
    document.rows[1].deposit_match_status = state
    assert [row['row_name'] for row in helper.detail_rows(document)] == ['A']
    stack, write, _, _ = environment(document)
    with stack, pytest.raises(frappe.ValidationError, match='ya no tiene diferencias'):
        helper.save('IMPORT', 'v1', [dict(row_name='A', difference_reason='Motivo', difference_notes='Soporte'),
            dict(row_name='B', difference_reason='Motivo', difference_notes='Soporte')])
    write.assert_not_called()


def test_inactive_ignored_and_settled_movements_are_excluded():
    for changes in (dict(effective=0), dict(match_status='Ignorado'),
                    dict(event_type='Deposito', unallocated_usd=0), dict(event_type='Ajuste')):
        row = frappe._dict(dict(event_type='Aplicacion', effective=1, match_status='Conciliado'), **changes)
        assert not helper.has_difference(row)
    assert helper.has_difference(frappe._dict(event_type='Deposito', effective=1, unallocated_usd=20))
    assert helper.has_difference(frappe._dict(event_type='Ajuste', effective=1, match_status='Ambiguo'))


def test_save_updates_only_reason_and_notes_preserving_money_statuses_and_links(document):
    before = deepcopy(document)
    stack, write, lock, reason = environment(document)
    with stack:
        result = helper.save('IMPORT', 'v1', [dict(row_name='A', difference_reason='Motivo', difference_notes='  Soporte  '),
            dict(row_name='B', difference_reason='Motivo', difference_notes='Nueva documentación')])
        assert result['updated'] == 2
        lock.assert_called_once_with(helper.DOCTYPE, 'IMPORT', 'name', for_update=True)
        reason.check_permission.assert_called_once_with('read')
        assert len(write.call_args_list) == 3
        for call in write.call_args_list:
            fields = set(call.args[2])
            assert fields <= ({'difference_reason', 'difference_notes'} if call.args[0] == 'CN Source Row' else {'modified', 'modified_by'})
        document.modified = result['modified']
        write.reset_mock()
        assert helper.save('IMPORT', document.modified, [dict(row_name='A', difference_reason='Motivo', difference_notes='Soporte')])['updated'] == 0
        write.assert_not_called()
    for previous, current in zip(before.rows, document.rows):
        ignored = {'difference_reason', 'difference_notes'}
        assert {k: v for k, v in previous.items() if k not in ignored} == {k: v for k, v in current.items() if k not in ignored}


@pytest.mark.parametrize('invalid', [
    dict(row_name='OTHER', difference_reason='Motivo', difference_notes='Soporte'),
    dict(row_name='B', difference_reason='MISSING', difference_notes='Soporte'),
    dict(row_name='B', difference_reason='', difference_notes='Soporte'),
    dict(row_name='B', difference_reason='Motivo', difference_notes=' '),
    dict(row_name='A', difference_reason='Motivo', difference_notes='Duplicado'),
    dict(row_name='B', difference_reason='Motivo', difference_notes='Soporte', amount_usd=0),
    dict(row_name='B', difference_reason='Motivo', difference_notes='Soporte', deposit_match_status='Conciliado'),
])
def test_entire_batch_validates_before_any_write(document, invalid):
    before = deepcopy(document)
    stack, write, _, _ = environment(document)
    with stack, pytest.raises(frappe.ValidationError):
        helper.save('IMPORT', 'v1', [dict(row_name='A', difference_reason='Motivo', difference_notes='Soporte'), invalid])
    write.assert_not_called()
    assert document == before


def test_stale_cancelled_and_unauthorized_requests_cannot_write(document):
    changes = [dict(row_name='A', difference_reason='Motivo', difference_notes='Soporte')]
    stack, write, _, reason = environment(document)
    with stack:
        with pytest.raises(frappe.ValidationError):
            helper.save('IMPORT', 'old', changes)
        document.docstatus = 2
        with pytest.raises(frappe.ValidationError):
            helper.save('IMPORT', 'v1', changes)
        with pytest.raises(frappe.ValidationError):
            helper.load('IMPORT')
        document.docstatus = 0
        with patch.object(Document, 'check_permission', side_effect=frappe.PermissionError):
            with pytest.raises(frappe.PermissionError):
                helper.save('IMPORT', 'v1', changes)
        with patch.object(reason, 'check_permission', side_effect=frappe.PermissionError):
            with pytest.raises(frappe.PermissionError):
                helper.save('IMPORT', 'v1', changes)
    write.assert_not_called()
