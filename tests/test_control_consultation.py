from unittest.mock import Mock, patch
import frappe
from credinomina_reconciliation import control_consultation as cache
from credinomina_reconciliation import company_statement
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


def test_consultation_reuses_read_and_isolates_user_scope_permissions_and_expiry():
    store = {}
    redis = Mock()
    redis.get_value.side_effect = store.get
    redis.set_value.side_effect = lambda key, value, **kw: store.update({key: value})
    builder = Mock(return_value={'work_items': [{'amount_usd': 40}]})
    with patch.object(cache.frappe, 'cache', redis), patch.object(cache, 'visibility_signature', return_value='visible') as signature, \
         patch.object(cache.frappe, 'session', frappe._dict(user='A')):
        rows, first = cache.read_consultation(None, {'employer': 'E'}, builder)
        _, repeated = cache.read_consultation(first['consultation'], {'employer': 'E'}, builder)
        assert not repeated['restarted'] and builder.call_count == 1
        assert redis.set_value.call_args.kwargs['expires_in_sec'] == 180
        _, changed_scope = cache.read_consultation(first['consultation'], {'employer': 'Other'}, builder)
        assert changed_scope['restarted']
        with patch.object(cache.frappe, 'session', frappe._dict(user='B')):
            assert cache.read_consultation(first['consultation'], {'employer': 'E'}, builder)[1]['restarted']
        signature.return_value = 'permission-revoked'
        assert cache.read_consultation(first['consultation'], {'employer': 'E'}, builder)[1]['restarted']
        signature.return_value = 'visible'
        store.clear()
        assert cache.read_consultation(first['consultation'], {'employer': 'E'}, builder)[1]['restarted']


def test_restarted_pagination_never_mixes_old_and_new_rows():
    rows = [dict(priority=1, target_name=str(i)) for i in range(250)]
    with patch.object(control, '_work_consultation', return_value=({'work_items': rows}, dict(restarted=True, consultation='new'))):
        page = control.get_control_rows('work_items', 'Todos', start=100, consultation='expired')
    assert page['rows'] == rows[:100]
    assert page['count'] == 250


def test_deposit_statement_drilldown_does_not_load_unrelated_ledgers():
    with patch.object(company_statement.frappe, 'has_permission', return_value=True), \
         patch.object(company_statement, 'load_application_context') as applications, \
         patch.object(company_statement, 'load_core_items') as core, \
         patch.object(company_statement, 'load_receivables') as receivables, \
         patch.object(company_statement, 'records', return_value=[]) as records:
        assert company_statement.load_detail({'component': 'deposit_pending_usd'}) == []
    applications.assert_not_called()
    core.assert_not_called()
    receivables.assert_not_called()
    assert records.call_count == 1
    assert records.call_args.args[0] == 'CN Remittance Allocation'
