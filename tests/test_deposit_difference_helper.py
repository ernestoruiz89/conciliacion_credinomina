from copy import deepcopy
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import deposit_difference_helper as helper


def row(name, **fields):
    return frappe._dict(dict(dict(name=name, doctype='CN Remittance Detail', idx=1, row_kind='Cliente',
        amount_usd=10, linked_usd=10, pending_usd=0, difference_reason='', difference_notes=''), **fields))


class Document(frappe._dict):
    def check_permission(self, permission):
        pass

    def add_comment(self, *args):
        pass


@pytest.fixture
def document():
    return Document(doctype=helper.DOCTYPE, name='DEP', modified='v1', docstatus=1,
        allocated_usd=20, allocation_detail='[{"importe_usd":20}]', detail_rows=[
            row('P', pending_usd=2, linked_usd=8), row('C', client_credit_usd=1),
            row('D', difference_reason='Anterior'), row('ZERO')])


def environment(document):
    stack = ExitStack()
    reason = Mock()
    stack.enter_context(patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: reason if doctype == 'CN Difference Reason' else document))
    stack.enter_context(patch.object(frappe.db, 'get_value'))
    stack.enter_context(patch.object(frappe.db, 'exists', side_effect=lambda doctype, name: name != 'Falta'))
    write = stack.enter_context(patch.object(frappe.db, 'set_value'))
    stack.enter_context(patch.object(frappe, 'clear_document_cache'))
    return stack, write, reason


def test_includes_pending_credits_and_documented_rows_but_not_settled_undocumented(document):
    document.detail_rows.append(row('EMP', row_kind='Diferencia documentada', difference_amount_usd=-.01,
                                    pending_usd=-.01, difference_core_pending_usd=.01))
    assert [value['row_name'] for value in helper.difference_rows(document)] == ['P', 'C', 'D', 'EMP']
    assert helper.difference_rows(document)[-1]['pending_usd'] == .01
    before = deepcopy(document)
    stack, write, reason = environment(document)
    with stack:
        assert len(helper.load('DEP')['rows']) == 4
        write.assert_not_called()
    assert document == before


def test_batch_updates_only_classification_and_can_be_edited_again(document):
    before = deepcopy(document)
    stack, write, reason = environment(document)
    with stack:
        result = helper.save('DEP', 'v1', [dict(row_name='P', difference_reason='Motivo'),
            dict(row_name='D', difference_reason='Otro', difference_notes='  Comentario  ')])
        assert result['updated'] == 2
        assert document.detail_rows[0].difference_notes == ''
        assert document.detail_rows[2].difference_notes == 'Comentario'
        assert reason.check_permission.call_count == 2
        assert len(write.call_args_list) == 3
        for call in write.call_args_list:
            assert set(call.args[2]) <= ({'difference_reason', 'difference_notes'} if call.args[0] == 'CN Remittance Detail' else {'modified', 'modified_by'})
        # No-op retries must neither touch the version nor add an audit event.
        document.modified = result['modified']
        write.reset_mock()
        result = helper.save('DEP', document.modified, [dict(row_name='P', difference_reason='Motivo')])
        assert result['updated'] == 0
        write.assert_not_called()
        edited = helper.save('DEP', document.modified, [dict(row_name='P', difference_reason='Otro', difference_notes='Editado')])
        assert edited['updated'] == 1
    assert document.allocated_usd == before.allocated_usd
    assert document.allocation_detail == before.allocation_detail
    assert document.detail_rows[0].pending_usd == before.detail_rows[0].pending_usd


@pytest.mark.parametrize('changes', [
    [dict(row_name='P', difference_reason='Motivo'), dict(row_name='AJENO', difference_reason='Motivo')],
    [dict(row_name='P', difference_reason='Motivo'), dict(row_name='D', difference_reason='Falta')],
    [dict(row_name='P', difference_reason='Motivo'), dict(row_name='D', difference_reason='')],
    [dict(row_name='P', difference_reason='Motivo'), dict(row_name='P', difference_reason='Motivo')],
    [dict(row_name='ZERO', difference_reason='Motivo')],
    [dict(row_name='P', difference_reason='Motivo', amount_usd=0)],
])
def test_invalid_batch_fails_before_any_write(document, changes):
    stack, write, reason = environment(document)
    with stack, pytest.raises(frappe.ValidationError):
        helper.save('DEP', 'v1', changes)
    write.assert_not_called()


def test_stale_cancelled_and_unauthorized_requests_cannot_write(document):
    stack, write, reason = environment(document)
    with stack:
        with pytest.raises(frappe.ValidationError):
            helper.save('DEP', 'old', [dict(row_name='P', difference_reason='Motivo')])
        document.docstatus = 2
        with pytest.raises(frappe.ValidationError):
            helper.save('DEP', 'v1', [dict(row_name='P', difference_reason='Motivo')])
        document.docstatus = 1
        with patch.object(Document, 'check_permission', side_effect=frappe.PermissionError):
            with pytest.raises(frappe.PermissionError):
                helper.save('DEP', 'v1', [dict(row_name='P', difference_reason='Motivo')])
        with patch.object(reason, 'check_permission', side_effect=frappe.PermissionError):
            with pytest.raises(frappe.PermissionError):
                helper.save('DEP', 'v1', [dict(row_name='P', difference_reason='Motivo')])
    write.assert_not_called()
