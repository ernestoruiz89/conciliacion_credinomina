"""Regression cases from the independent October operational review."""
import copy
import inspect
import json
from datetime import date
from unittest.mock import patch, Mock

import frappe
import pytest

from credinomina_reconciliation import historical_cutoff as cutoff
from credinomina_reconciliation import company_statement as statement
from credinomina_reconciliation import receivable_recovery as recovery
from credinomina_reconciliation.cutoff_evidence import require_cutoff_evidence
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


def reject(message, error=frappe.ValidationError, **kwargs):
    raise error(message)


def context():
    return ([dict(name='APP', parent='IMP', event_date='2025-04-30', event_type='Aplicacion',
                  effective=1, currency='USD', amount=100, match_status='Conciliado',
                  historical_period='P', historical_remitted_usd=100)],
            {'IMP': dict(name='IMP', employer='E')},
            {'P': dict(name='P', employer='E', payroll_month='2025-04-01', reconciliation_mode='Historica')},
            {}, {'E': dict(grace_days=10)})


def deposit(**values):
    return frappe._dict(dict(name='D', employer='E', docstatus=1, amount_usd=100,
        deposit_date='2025-04-30', allocated_usd=100, justified_surplus_usd=0,
        result='Conciliado', detail_status='Conciliado',
        allocation_detail=json.dumps([dict(tipo='Aplicacion historica', aplicacion_id='APP', importe_usd=100)])), **values)


def test_cutoff_load_checks_hidden_deposit_before_reconstructing_paid_debt():
    inputs = context()
    before = copy.deepcopy(inputs)
    hidden = deposit(name='SECRET-DEP')
    for visible in (False, True):
        with patch.object(cutoff, 'load_application_context', return_value=inputs), \
                patch.object(cutoff, 'records', side_effect=lambda dt, **kw: [hidden] if visible and dt == 'CN Remittance Allocation' else []), \
                patch.object(cutoff, 'child_records', return_value=[]), \
                patch.object(frappe, 'has_permission', return_value=True), \
                patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: [hidden] if dt == 'CN Remittance Allocation' else []), \
                patch.object(frappe, 'throw', side_effect=reject):
            if visible:
                assert cutoff.load_cutoff({'cutoff_date': '2025-05-31'})['summary'] == []
            else:
                with pytest.raises(frappe.PermissionError, match='Saldo no determinable') as error:
                    cutoff.load_cutoff({'cutoff_date': '2025-05-31'})
                assert 'SECRET' not in str(error.value)
    assert inputs == before


@pytest.mark.parametrize('link', [dict(related_application='APP'), dict(receivable_origin='ORIGIN'),
    dict(registered_deposit='D'), dict(claim_id='H:APP'), dict(accounting_exception='EXC')])
def test_hidden_complementary_dependencies_fail_closed_without_disclosure(link):
    hidden = frappe._dict(name='SECRET-ITEM', posting_date='2025-04-30', **link)
    with patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: [hidden] if dt == 'CN Complementary Item' else []), \
            patch.object(frappe, 'throw', side_effect=reject), pytest.raises(frappe.PermissionError) as error:
        require_cutoff_evidence(context(), [deposit()], [dict(name='ORIGIN', registration_exception='EXC')], date(2025,5,31))
    assert 'SECRET' not in str(error.value)


def test_unrelated_and_future_hidden_items_do_not_block():
    unrelated = frappe._dict(name='OTHER', posting_date='2025-04-30', related_application='OTHER-APP')
    future = frappe._dict(name='FUTURE', posting_date='2025-06-01', related_application='APP')
    def all_rows(dt, **kw):
        if dt == 'CN Complementary Item':
            return [unrelated, future]
        assert kw['filters']['deposit_date'] == ['<=', '2025-05-31']
        return [deposit(name='OTHER-DEP', allocation_detail='[]')]
    with patch.object(frappe, 'get_all', side_effect=all_rows):
        require_cutoff_evidence(context(), [], [], date(2025,5,31))


def test_hidden_shared_operational_deposit_is_checked_by_collection_key():
    inputs = context()
    inputs[3]['ROW'] = dict(name='ROW', parent='P', row_key='KEY')
    hidden = deposit(name='PAYER-SECRET', employer='PAYER', allocation_detail=json.dumps([
        dict(tipo='Cobranza', periodo='P', fila_id='KEY', importe_usd=100)]))
    with patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: [hidden] if dt == 'CN Remittance Allocation' else []), \
            patch.object(frappe, 'throw', side_effect=reject), pytest.raises(frappe.PermissionError):
        require_cutoff_evidence(inputs, [], [], date(2025,5,31))


@pytest.mark.parametrize('result,detail', [('Revisar detalle','Revisar filas'), ('Conciliado','Revisar filas'),
    ('Pendiente','Revisar filas'), ('Revisar destinos','Conciliado'), ('Detalle pendiente','')])
def test_fully_allocated_deposit_review_stays_visible_without_debt(result, detail):
    original = deposit(result=result, detail_status=detail)
    before = copy.deepcopy(original)
    rows = statement.build_detail([], [], [], [original])
    assert len(rows) == 1
    assert rows[0]['balance_usd'] == rows[0]['deposit_pending_usd'] == 0
    assert rows[0]['status'] in {'Revisar detalle', 'Por revisar', 'Revisar destinos', 'Detalle pendiente'}
    assert statement.summarize(rows)[0]['pending_count'] == 1
    assert original == before


@pytest.mark.parametrize('values,observed', [({'allocated_usd':110},10),
    ({'justified_surplus_usd':10},10), ({'allocated_usd':-1},100),
    ({'allocated_usd':90,'justified_surplus_usd':-1},100), ({'amount_usd':0,'allocated_usd':0},0)])
def test_invalid_distribution_is_not_a_collectible_balance(values, observed):
    rows = statement.build_detail([], [], [], [deposit(**values)])
    assert rows[0]['status'] == 'Revisar distribución'
    assert rows[0]['deposit_pending_usd'] is None
    assert rows[0]['observed_usd'] == observed
    assert 'no determinable' in rows[0]['observation']
    assert statement.summarize(rows)[0]['balance_usd'] is None


def test_valid_deposit_remainders_and_settled_exclusions_are_unchanged():
    assert statement.build_detail([],[],[],[deposit()]) == []
    rows = statement.build_detail([],[],[],[deposit(allocated_usd=60,justified_surplus_usd=10)])
    assert rows[0]['deposit_pending_usd'] == -30
    assert statement.build_detail([],[],[],[deposit(docstatus=2)]) == []
    assert statement.build_detail([],[],[],[deposit(docstatus=0)]) == []
    rows = statement.build_detail([],[],[],[deposit(docstatus=0, accounting_source_key='CORE')])
    assert rows[0]['deposit_pending_usd'] == -100


def test_unknown_collection_conversion_generates_nonfinancial_task():
    row = frappe._dict(expected_usd=0, expected_nio=3662.43, applied_usd=100,
                       remitted_usd=100, application_status='Aplicado y remitido')
    period = dict(name='P', employer='E', reconciliation_mode='Operativa', financial_pending_usd=0,
                  worker_gap_usd=0, rows=[row], exceptions=[], historical_rows=[])
    for payload in (period, dict(period, rows=[], quality_missing_basis_count=1)):
        tasks = control._build_work_items([payload], [], [], [], [], [], {'E':'Empresa'}, as_of=date(2026,10,12))
        assert len(tasks) == 1
        assert tasks[0]['kind'] == 'quality_missing_basis'
        assert tasks[0]['amount_usd'] is None and tasks[0]['count'] == 1
        assert 'No modifica la CxC' in tasks[0]['next_action']


def test_selector_skips_thousand_settled_deposits_without_loading_documents():
    rows = [deposit(name=f'D-{i}') for i in range(1000)]
    with patch.object(recovery, '_recovery_scope'), patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={}), \
            patch('credinomina_reconciliation.report_records.records', return_value=rows), \
            patch.object(frappe, 'get_doc') as read, patch.object(frappe, 'get_all') as related:
        assert inspect.unwrap(recovery.search_recovery_destinations)(
            'CN Remittance Allocation', '', '', 0, 20, {'item_name':'ORIGIN','employer':'E','method':'Depósito'}) == []
        read.assert_not_called()
        related.assert_not_called()


def test_selector_batches_reservations_closed_periods_and_cancelled_destinations():
    rows = [deposit(name=name, allocated_usd=0, allocation_detail=json.dumps(journal)) for name, journal in (
        ('RESERVED', []), ('CLOSED', [dict(partida='COMP')]), ('INVALID', []), ('AVAILABLE', []))]
    targets = [frappe._dict(parent='RESERVED', amount_usd=100, result=''),
               frappe._dict(parent='INVALID', complementary_item='CANCELLED')]
    def related(dt, **kw):
        if dt == 'CN Complementary Item':
            return [frappe._dict(name='COMP',period='P',docstatus=1),frappe._dict(name='CANCELLED',docstatus=2)]
        return ['P']
    with patch('credinomina_reconciliation.report_records.child_records', return_value=targets), \
            patch.object(frappe, 'get_all', side_effect=related) as read:
        assert [r.name for r in recovery._prefilter_recovery_candidates(rows, 'Depósito')] == ['AVAILABLE']
        assert read.call_count == 2


def test_selector_preserves_authorization_and_validation_for_available_documents():
    rows = [deposit(name=name, allocated_usd=0, allocation_detail='[]') for name in ('DENIED','AVAILABLE')]
    with patch.object(recovery, '_recovery_scope'), patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={}), \
            patch('credinomina_reconciliation.report_records.records', return_value=rows), \
            patch('credinomina_reconciliation.report_records.child_records', return_value=[]), \
            patch.object(frappe, 'get_doc', side_effect=rows), \
            patch.object(recovery, '_candidate_value', side_effect=[frappe.PermissionError(), money(100)]) as validate:
        result = inspect.unwrap(recovery.search_recovery_destinations)(
            'CN Remittance Allocation', '', '', 0, 20, {'item_name':'ORIGIN','employer':'E','method':'Depósito'})
        assert [row[0] for row in result] == ['AVAILABLE']
        assert validate.call_count == 2


def test_selector_skips_compensated_credit_items_in_batches():
    rows = [frappe._dict(name=f'C-{i}', docstatus=0, employer='E', category='Compensación entre partidas',
                        amount_usd=-10, posting_date='2025-04-30') for i in range(250)]
    offsets = [frappe._dict(parent=row.name, amount_usd=10) for row in rows]
    with patch('credinomina_reconciliation.report_records.child_records', return_value=offsets) as ledger, \
            patch.object(frappe, 'get_all', return_value=[]):
        assert recovery._prefilter_recovery_candidates(rows, 'Compensación') == []
        ledger.assert_called_once()
        assert ledger.call_args.kwargs['filters']['compensation_date'][0] == '<='


def test_quality_missing_basis_is_identical_in_full_and_lazy_control():
    period = frappe._dict(name='P', employer='E', payroll_month='2026-09-01',
        reconciliation_mode='Operativa', status='Conciliado', applied_usd=100, remitted_usd=100)
    row = frappe._dict(name='R', parent='P', expected_usd=0, expected_nio=3662.43,
        applied_usd=100, remitted_usd=100, application_status='Aplicado y remitido')
    for summary in (True, False):
        with patch.object(frappe, 'has_permission', return_value=True), \
                patch.object(frappe, 'get_list', side_effect=lambda dt, **kw: [period] if dt == 'CN Reconciliation Period' else []), \
                patch.object(frappe, 'get_all', side_effect=lambda dt, **kw: [row] if dt == 'CN Collection Row' else []), \
                patch.object(control, 'collection_summaries', return_value={'P':dict(quality_missing_basis_count=1)}), \
                patch.object(control, 'historical_difference_counts', return_value={'P':0}):
            result = control._build_control_data('Todos', summary_only=summary, detail_period='P')['periods'][0]
        assert result['quality_missing_basis_count'] == 1
        assert result['worker_gap_usd'] == result['financial_pending_usd'] == 0


def test_invalid_deposit_propagates_unknown_to_excel_summary():
    import io
    from datetime import datetime
    from openpyxl import load_workbook
    from credinomina_reconciliation.control_export import build_control_workbook
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report
    detail = statement.build_detail([],[],[],[deposit(allocated_usd=110)])
    with patch.object(report, 'load_detail', return_value=detail):
        columns, rows, message = report.execute({'view_mode':'Resumen'})
    payload = build_control_workbook({'year':'Todos','periods':[],
        'company_statement':dict(columns=columns,rows=rows,message=message)}, exceptions=[], actions=[],
        employer_label='E', generated_at=datetime(2026,10,12))
    sheet = load_workbook(io.BytesIO(payload))['Resumen']
    values = dict(zip([cell.value for cell in sheet[4]], [cell.value for cell in sheet[5]]))
    assert values['Saldo neto informativo US$'] == 'N/D'
    assert values['Depósito sin conciliar US$'] == 'N/D'
    assert values['Partidas pendientes'] == 1


def test_collection_export_does_not_present_missing_usd_as_zero():
    from credinomina_reconciliation.control_export import _collection_detail, NA
    row = dict(expected_usd=0, expected_nio=3662.43, applied_usd=100)
    detail = _collection_detail(dict(name='P', employer_name='E'), row)
    assert detail[13] == NA
    assert detail[20] == 'Revisar base de comparación'
    row.update(expected_usd=100)
    assert _collection_detail(dict(name='P', employer_name='E'), row)[13] == 100
