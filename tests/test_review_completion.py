"""Cross-view review: warnings are not debt; preview is never a confirmation."""
import json
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import client_position, company_statement
from credinomina_reconciliation.application_aging import balance_status, application_balances
from credinomina_reconciliation.application_adjustments import cash_coverage
from credinomina_reconciliation.application_candidate_context import CandidateContext
from credinomina_reconciliation.application_context import linked_application_employer
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.complementary_transactions import progress
from credinomina_reconciliation.core_item_position import signed_pending
from credinomina_reconciliation.control_export import _core_item_needs_follow_up
from credinomina_reconciliation.follow_up_queue import build_follow_up
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as operational
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement


@pytest.mark.parametrize('paid,remaining,state', [(0,100,'Pendiente'),(60,40,'Parcial'),(100,0,'Conciliado'),(110,0,'Revisar distribución')])
def test_application_states_agree(paid, remaining, state):
    source = dict(applied_usd=100, paid_usd=paid, amount_usd=remaining, employer='A', source_import='I')
    assert balance_status(source) == state
    rows = client_position.build_position([], [source], [], {}, [], {})
    assert rows[0]['operational_status'] == state
    detail = company_statement.build_detail([source], [], [], [])
    assert len(detail) == (0 if state == 'Conciliado' else 1)
    if detail:
        assert detail[0]['status'] == state
    if state == 'Revisar distribución':
        assert company_statement.build_detail([source], [], [], [], {'component':'application_pending_usd'})


def test_company_query_keeps_review_without_loading_all_settled_outputs():
    rows = [dict(name='A'+str(i), parent='I', effective=1, event_type='Aplicacion', currency='USD', amount=100,
        match_status='Conciliado', historical_period='P', historical_remitted_usd=paid, event_date='2026-09-01')
        for i, paid in enumerate([100, 110, 60])]
    inputs = (rows, {'I':dict(name='I')}, {'P':dict(name='P', employer='E')}, {}, {'E':{}}, '2026-10-12')
    assert len(application_balances(*inputs)) == 1
    result = application_balances(*inputs, include_reviews=True)
    assert [balance_status(row) for row in result] == ['Revisar distribución', 'Parcial']


def legacy_item(**values):
    return dict(dict(name='C', docstatus=1, employer='A', amount_usd=50, category='Otros ingresos',
        accounting_source_key='original', source_debit=100, source_currency='USD',
        accounting_status='Importada del core'), **values)


@pytest.mark.parametrize('used', [0, 60, 100])
def test_inconsistent_original_visible_with_no_invented_receivable(used):
    item = legacy_item()
    balance = financial_balance(item, [dict(amount_usd=used)])
    core = dict(item, _balance=balance, _signed_pending_usd=signed_pending(item, balance))
    rows = company_statement.build_detail([], [core], [], [], {'component':'core_pending_usd'})
    assert rows[0]['core_pending_usd'] == 100-used
    assert rows[0]['status'] == 'Revisar distribución'
    assert 'evidencia' in rows[0]['observation']
    assert company_statement.summarize(rows)[0]['pending_count'] == 1
    assert client_position.build_position([], [], [item], {'C':balance}, [], {'only_open':1})
    task = build_follow_up([item], {'C':balance}, [])[0]
    assert (task['amount_usd'], task['amount_kind']) == (100, 'Importe observado')
    assert _core_item_needs_follow_up(balance)


def test_draft_transaction_uses_original_magnitude_and_warning():
    item = legacy_item(docstatus=0)
    result = progress(item, financial_balance(item))
    assert (result['original_usd'], result['pending_usd']) == (100, 100)
    assert result['state'] == 'Revisar distribución'
    assert result['resolved_usd'] is None


def test_real_document_parent_does_not_require_dictionary_subscription():
    from frappe.model.document import Document
    parent = object.__new__(Document)  # Exercise the real get API without metadata/SQL.
    parent.employer = 'EMP'
    assert linked_application_employer({}, parent) == 'EMP'


@pytest.mark.parametrize('state', ['Conciliada','Documentado','Vigente','Registro contable verificado'])
def test_resolved_core_evidence_does_not_pollute_actionable_export(state):
    balance = dict(financial_status=state, pending_usd=0, accounting_status='Importada del core')
    assert not _core_item_needs_follow_up(balance)
    assert _core_item_needs_follow_up(dict(balance, management_pending_usd=1))


def test_classification_options_come_from_real_data_without_mutation():
    detail = company_statement.build_detail([], [], [], [], adjustment_receivables=[
        dict(name='C', employer='A', receivable_usd=20, receivable_status='Parcialmente cobrada'),
        dict(name='D', employer='A', receivable_usd=30, receivable_status='Pendiente de cobro')])
    from credinomina_reconciliation.company_accounting_statement import management_detail
    rows = statement._presentation(management_detail(detail), {'status':'Parcialmente cobrada'})
    assert rows[0]['company_receivable_usd'] == 20
    assert rows[0]['_available_statuses'] == ['Parcialmente cobrada', 'Pendiente de cobro']
    assert not any('_available_statuses' in row for row in detail)


def test_compact_columns_keep_values_and_expansion_exposes_audit():
    compact = operational.get_columns({'position_type':'Aplicación'})
    full = operational.get_columns({'position_type':'Aplicación', 'detailed_columns':1})
    assert sum(not c.get('hidden') for c in compact) <= 10
    assert {c['fieldname'] for c in compact} == {c['fieldname'] for c in full}
    assert next(c for c in full if c['fieldname']=='source_rows').get('hidden') is None


def test_collection_only_does_not_load_unrelated_financial_ledgers():
    with patch.object(client_position, 'load_application_context') as applications, \
            patch.object(client_position, 'records') as records, \
            patch('credinomina_reconciliation.deposit_adjustment_receivables.load_receivables') as receivables:
        assert client_position.load_position([], {'position_type':'Cobranza'}) == []
        applications.assert_not_called()
        records.assert_not_called()
        receivables.assert_not_called()


def test_batched_candidates_use_actual_cash_or_reservation_once_and_cannot_confirm():
    rows = [frappe._dict(name='A'+str(i), amount=100, currency='USD', historical_period='P') for i in range(200)]
    item = frappe._dict(name='C')
    parent = frappe._dict(employer='EMP')
    target = frappe._dict(name='T', parent='D', historical_application='A0', amount_usd=60)
    deposit = frappe._dict(name='D', allocation_detail=json.dumps([dict(tipo='Aplicacion historica', aplicacion_id='A0', importe_usd=50)]))
    def query(dt, **kwargs):
        return {'CN Reconciliation Period':[frappe._dict(name='P', status='Pendiente')],
            'CN Remittance Target':[target], 'CN Remittance Allocation':[deposit]}.get(dt, [])
    with patch.object(frappe, 'get_all', side_effect=query) as reads, \
            patch.object(frappe.db, 'exists', return_value=False), \
            patch.object(frappe.db, 'get_value') as per_row:
        context = CandidateContext(rows, parent, item)
        assert cash_coverage(rows[0], context=context)['adjustable_usd'] == 40
        assert cash_coverage(rows[1], context=context)['adjustable_usd'] == 100
        assert reads.call_count <= 4
        per_row.assert_not_called()
        with pytest.raises(ValueError):
            cash_coverage(rows[0], lock=True, context=context)


def test_closed_secondary_period_and_reserved_item_block_batch_preview():
    row=frappe._dict(name='A', amount=100, currency='USD', application_allocation_detail='[{"collection_row_id":"R","amount_usd":100}]')
    def query(dt, **kwargs):
        return {'CN Collection Row':[frappe._dict(name='R', parent='P', row_key='K', applied_usd=100)],
                'CN Reconciliation Period':[frappe._dict(name='P', status='Cerrado')]}.get(dt, [])
    with patch.object(frappe, 'get_all', side_effect=query), patch.object(frappe.db, 'exists', return_value=False):
        context = CandidateContext([row], frappe._dict(employer='E'), frappe._dict(name='C'))
        assert 'P' in context.block_reason(row)
        context.statuses['P']='Pendiente'
        assert context.block_reason(row) is None
        context.reserved_item=True
        assert 'destino' in context.block_reason(row)


def test_batch_preview_respects_readable_legacy_period_identity():
    row = frappe._dict(name='A', historical_period='P', amount=100, currency='USD')
    with patch.object(frappe, 'get_all', return_value=[]), patch.object(frappe.db, 'exists', return_value=False), \
            patch('credinomina_reconciliation.application_candidate_context.records', return_value=[]) as readable:
        context = CandidateContext([row], frappe._dict(employer=''), frappe._dict(name='C'))
        assert context.employer(row) == ''
        readable.return_value = [frappe._dict(name='P', employer='EMP')]
        context = CandidateContext([row], frappe._dict(employer=''), frappe._dict(name='C'))
        assert context.employer(row) == 'EMP'
