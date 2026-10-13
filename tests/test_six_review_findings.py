"""Regression proof for the six reviewed reporting/operation findings."""
from copy import deepcopy
from unittest.mock import patch
import json

import frappe
import pytest

from credinomina_reconciliation.historical_cutoff import reconstruct
from credinomina_reconciliation.application_aging import balance_status
from credinomina_reconciliation.company_transactions import application_progress_index
from credinomina_reconciliation.accounting_control import build_rows, monetary_totals, summarize
from credinomina_reconciliation.period_pending import application_issue
from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.paying_employers import choose_detail_client
from credinomina_reconciliation.remittance_credit_selection import complete_detail_clients
from credinomina_reconciliation.accounting_preview import review_page, unidentified_rows, get_unidentified_page, save_preview
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables import control_mensual_de_movimientos_contables as control


def application_context():
    row = dict(name='R', parent='I', idx=1, event_type='Aplicacion', effective=1, currency='USD', amount=100,
        event_date='2025-04-15', match_status='Conciliado', client_name='Ana', client_number='1',
        loan_number='10-1', collection_row_id='C', source_row=257, source_account='160209013004',
        source_currency='USD', source_debit=100, source_credit=0)
    return ([row], {'I':dict(name='I', status='Importado', employer='A')},
            {'P':dict(name='P', employer='A', reconciliation_mode='Operativa', payroll_month='2025-04-01')},
            {'C':dict(name='C', parent='P', row_key='CK', applied_usd=100, remittance_detail=[])}, {'A':dict(grace_days=10)})


@pytest.mark.parametrize('links,capacity', [
    ([dict(collection_row_id='C', amount_usd=150)], 150),
    ([dict(collection_row_id='C', amount_usd=-10), dict(collection_row_id='C', amount_usd=110)], 100),
    ([dict(collection_row_id='C', amount_usd=100)], 50), ('invalid JSON', 100),
])
def test_cutoff_does_not_repair_bad_current_evidence(links, capacity):
    context = application_context()
    context[0][0]['application_allocation_detail'] = links
    context[3]['C']['applied_usd'] = capacity
    before = deepcopy(context)
    cut = reconstruct(context, [], [], [], '2025-05-01')
    assert len(cut['applications']) == 1
    row = cut['applications'][0]
    assert row['applied_usd'] == 100 and row['amount_usd'] is None
    assert balance_status(row) == 'Revisar distribución'
    assert cut['warnings'] and cut['summary'][0]['balance_usd'] is None
    assert context == before


def test_cutoff_restores_only_proven_dated_adjustment_and_shared_capacity():
    context = application_context()
    context[0][0].update(application_adjustment_usd=20,
                         application_allocation_detail=[dict(collection_row_id='C', amount_usd=80)])
    context[3]['C']['applied_usd'] = 80
    item = dict(name='ADJ', docstatus=1, category='Ajuste de aplicación', posting_date='2025-05-01',
                related_application='R', application_adjustment_usd=20, amount_usd=20, employer='A')
    before = deepcopy(context)
    early = reconstruct(context, [], [item], [], '2025-04-30')['applications'][0]
    late = reconstruct(context, [], [item], [], '2025-05-01')['applications'][0]
    assert (early['applied_usd'], early['amount_usd']) == (100, 100)
    assert (late['applied_usd'], late['amount_usd']) == (80, 80)
    assert not early.get('distribution_issue') and not late.get('distribution_issue')
    assert context == before


def test_shared_overlinked_sources_remain_unknown_in_cut():
    context = application_context()
    context[0].append(dict(context[0][0], name='R2', amount=100))
    cut = reconstruct(context, [], [], [], '2025-05-01')
    assert len(cut['applications']) == 2
    assert all(balance_status(row) == 'Revisar distribución' for row in cut['applications'])


@pytest.mark.parametrize('paid,adjustment,expected,pending', [(80, 0, 'Parcial', 20), (80, 20, 'Conciliado', 0), (0, 100, 'Conciliado', 0)])
def test_labels_cannot_hide_current_application_balance(paid, adjustment, expected, pending):
    context = application_context()
    row = context[0][0]
    row.update(historical_period='P', processing_route='Historica', application_adjustment_usd=adjustment,
               historical_remitted_usd=paid, deposit_match_status='Depósito conciliado')
    progress = application_progress_index(context[0], context[1], context[3])
    result = build_rows(context[0], context[1], [], progress=progress)[0][0]
    assert result['state'] == expected and result['pending_usd'] == pending
    issue = application_issue(row, progress['R'])
    assert (issue is None) == (pending == 0)
    if issue:
        assert issue['pending'] == 20


def test_control_operational_shared_capacity_and_unknown_conversion():
    context = application_context()
    context[3]['C']['remittance_detail'] = [dict(importe_usd=80)]
    context[0][0]['deposit_match_status'] = 'Depósito conciliado'
    progress = application_progress_index(context[0], context[1], context[3])
    assert build_rows(context[0], context[1], [], progress=progress)[0][0]['state'] == 'Parcial'
    context[0][0]['currency'] = 'NIO'
    progress = application_progress_index(context[0], context[1], context[3])
    issue = application_issue(context[0][0], progress['R'])
    assert issue['pending'] is None and issue['status'] == 'Revisar distribución'


def test_totals_unknown_only_in_affected_currency_and_keep_known_subtotal():
    rows = [dict(source_currency='NIO', debit_nio=100, credit_nio=0, net_nio=100,
                 debit_usd=10, credit_usd=0, net_usd=10),
            dict(source_currency='NIO', debit_nio=50, credit_nio=0, net_nio=50,
                 debit_usd=None, credit_usd=None, net_usd=None),
            dict(source_currency='USD', debit_nio=None, credit_nio=None, net_nio=None,
                 debit_usd=20, credit_usd=0, net_usd=20)]
    result = monetary_totals(rows)
    assert result['debit_nio'] == 150 and result['credit_nio'] == 0
    assert result['debit_usd'] is None and result['credit_usd'] is None
    assert result['debit_usd_known'] == 30 and result['debit_usd_missing'] == 1
    summary_rows = [dict(row, month='2025-04', source_account='1602', missing_conversion=int(row['debit_usd'] is None)) for row in rows]
    nio = next(row for row in summarize(summary_rows) if row['source_currency'] == 'NIO')
    assert nio['debit_usd'] is None and nio['debit_usd_known'] == 10
    assert monetary_totals([])['debit_usd'] == 0


def test_report_unknown_kpis_and_summary_export_keep_unknown_not_zero():
    context = application_context()
    row = dict(context[0][0], source_currency='NIO', source_fx_rate=0)
    context[0][:] = [row]
    def read(doctype, **kwargs):
        return [frappe._dict(context[1]['I'])] if doctype == 'CN Accounting Import' else []
    with patch.object(frappe, 'has_permission', return_value=True), patch.object(frappe, 'get_list', side_effect=read), \
            patch.object(frappe, 'get_all', side_effect=lambda doctype, **kwargs: [frappe._dict(row)] if doctype == 'CN Source Row' else []), \
            patch('credinomina_reconciliation.application_context.load_application_context', return_value=context):
        columns, rows, message, _, kpis = control.execute({'from_date':'2025-04-01', 'to_date':'2025-04-30', 'summary':1})
    assert rows[0]['debit_usd'] is None
    assert next(kpi for kpi in kpis if kpi['label'] == 'Débitos US$')['value'] == 'No determinado'
    assert next(kpi for kpi in kpis if kpi['label'] == 'Débitos C$')['value'] == 100
    assert any(column['fieldname'] == 'debit_usd_known' for column in columns)
    assert 'NO son el total' in message
    assert len(kpis) == 5 and 'Ver subtotales conocidos' in message and 'USD 0.00' in message


@pytest.mark.parametrize('identity', [{'loan_number':'10'}, {'client_number':'1'}, {'national_id':'ID1'}, {'client_name':'Ana'}])
def test_previous_employer_identity_uses_evidence_not_current_registry(identity):
    clients = [dict(name='1', client_number='1', national_id='ID1', client_name='Ana', employer='B', employee_number='NEW')]
    portfolio = frappe._dict(name='PR', credit_number='10-1', matched_client='1', client_number_core='1', national_id='ID1', client_name='Ana', employer='A')
    def read(doctype, **kwargs):
        return {'CN Credit Portfolio Snapshot':['CUT'], 'CN Credit Portfolio Row':[portfolio], 'CN Accounting Import':[]}[doctype]
    original = deepcopy(clients)
    with patch.object(frappe, 'get_all', side_effect=read), patch('credinomina_reconciliation.deposit_identity.get_credit_rows', return_value=[portfolio]):
        evidence = load_detail_loan_clients([identity], clients, {'A'})
    client, reason = choose_detail_client(dict(identity, employer='A'), clients, 'A', {'A'}, evidence)
    assert client['name'] == '1' and client['employer'] == 'A'
    assert clients == original
    assert choose_detail_client(dict(identity, employer='C'), clients, 'A', {'A'}, evidence)[0] is None
    assert choose_detail_client(dict(identity, employee_number='NEW'), clients, 'A', {'A'}, evidence)[0] is None
    assert choose_detail_client(dict(identity, national_id='OTHER'), clients, 'A', {'A'}, evidence)[0] is None
    row = frappe._dict(identity)
    complete_detail_clients([row], clients, 'A', {'A'}, evidence)
    assert (row.client, row.employer, row.client_number) == ('1', 'A', '1')


def test_historical_company_is_not_inferred_from_global_client_alone():
    clients = [dict(name='1', client_number='1', client_name='Ana', employer='B')]
    assert choose_detail_client({'client_number':'1'}, clients, 'A', {'A'}, {'10-1':{'1'}})[0] is None


def test_payer_for_two_companies_infers_the_unique_loan_company():
    from credinomina_reconciliation.deposit_identity import DetailIdentityEvidence
    from credinomina_reconciliation.client_identity import ClientIdentityIndex
    current = dict(name='1', client_number='1', client_name='Ana', employer='B')
    historical = dict(current, employer='A')
    evidence = DetailIdentityEvidence()
    evidence.update({'10-1':{'1'}})
    evidence.current_index = ClientIdentityIndex([current])
    evidence.company_clients['A']['1'] = historical
    evidence.historical_index = ClientIdentityIndex([historical])
    evidence.loan_employers['10-1'].add('A')
    client, _ = choose_detail_client({'loan_number':'10'}, [current], 'PAYER', {'A', 'B'}, evidence)
    assert client and client['employer'] == 'A'
    assert choose_detail_client({'loan_number':'10', 'employer':'B'}, [current], 'PAYER', {'A', 'B'}, evidence)[0] is None


@pytest.mark.parametrize('current_company', ['A', 'B'])
def test_historical_employee_number_is_company_specific_evidence(current_company):
    clients = [dict(name='1', client_number='1', client_name='Ana', employer=current_company, employee_number='NEW')]
    application = frappe._dict(name='R', parent='I', loan_number='10-1', client='1', client_number='1',
                               client_name='Ana', employee_number='OLD')
    def read(doctype, **kwargs):
        return {'CN Credit Portfolio Snapshot':[], 'CN Accounting Import':[frappe._dict(name='I', employer='A')],
                'CN Source Row':[application]}[doctype]
    with patch.object(frappe, 'get_all', side_effect=read):
        evidence = load_detail_loan_clients([{'employee_number':'OLD'}], clients, {'A'})
    client, _ = choose_detail_client({'employee_number':'OLD'}, clients, 'A', {'A'}, evidence)
    assert client and client['name'] == '1' and client['employee_number'] == 'OLD'
    assert (choose_detail_client({'employee_number':'NEW'}, clients, 'A', {'A'}, evidence)[0] is None) == (current_company != 'A')


def test_cutoff_ignored_application_cannot_consume_a_deposit():
    context = application_context()
    context[0][0]['match_status'] = 'Ignorado'
    deposit = dict(name='D', employer='A', docstatus=1, deposit_date='2025-04-15', amount_usd=100,
                   allocation_detail=[dict(tipo='Cobranza', periodo='P', fila_id='CK', importe_usd=100)])
    cut = reconstruct(context, [deposit], [], [], '2025-05-01')
    assert cut['applications'] == []
    assert cut['summary'][0]['deposit_pending_usd'] == -100


def test_unidentified_pages_include_every_row_and_choices_without_reanalysis():
    plan = dict(groups=[dict(employer='NO IDENTIFICADA', rows=[dict(source_row=i, client_name='José', amount_usd=1, voucher=f'V{i}')]) for i in range(1,202)],
                complementary=[dict(source_row=202, resolved_employer='NO IDENTIFICADA', source_description='Reclasificación')],
                deposits=[dict(source_row=203, resolved_employer='NO IDENTIFICADA', bank_deposit_reference='DEP')])
    all_rows = list(unidentified_rows(plan))
    chunks = [all_rows[:100], all_rows[100:]]
    choices = {'151':'Empresa A', '202':'Empresa B'}
    seen = []
    for start in range(0, 203, 50):
        page = review_page(iter(chunks), choices, start=start)
        assert page['count'] == 203
        seen += [row['row'] for row in page['rows']]
    assert seen == list(range(1,204))
    assert review_page(iter(chunks), choices, search='Empresa A')['rows'][0]['row'] == 151
    assert review_page(iter(chunks), choices, search='reclasificacion')['rows'][0]['row'] == 202
    assert review_page(iter(chunks), choices, kind='Depósito')['rows'][0]['row'] == 203
    assert review_page(iter(chunks), choices, search='jose')['count'] == 201
    with patch('credinomina_reconciliation.accounting_preview.insert_internal') as insert, patch.object(frappe.db, 'get_value', return_value=None):
        assert save_preview('TOKEN', plan) == 1
        assert insert.call_args.kwargs['block_index'] == -1
        assert len(json.loads(insert.call_args.kwargs['payload'])['review_rows']) == 203


def test_preview_permission_owner_checked_before_reading_any_chunk():
    with patch('credinomina_reconciliation.bulk_accounting_import._state', side_effect=PermissionError), patch.object(frappe.db, 'get_value') as read:
        with pytest.raises(PermissionError):
            get_unidentified_page('OTHER')
        read.assert_not_called()


def test_preview_checkpoint_retry_is_idempotent_and_changed_evidence_rejected():
    plan = dict(groups=[dict(employer='NO IDENTIFICADA', rows=[dict(source_row=1, amount_usd=1)])])
    saved = {}
    def insert(doctype, name, **values):
        saved[name] = values['payload']
    with patch('credinomina_reconciliation.accounting_preview.insert_internal', side_effect=insert) as create, \
            patch.object(frappe.db, 'get_value', side_effect=lambda doctype, name, field:saved.get(name)), \
            patch.object(frappe, 'throw', side_effect=ValueError):
        assert save_preview('T', plan) == save_preview('T', plan) == 1
        assert create.call_count == 1
        plan['groups'][0]['rows'][0]['amount_usd'] = 2
        with pytest.raises(ValueError):
            save_preview('T', plan)


def test_compact_grid_keeps_original_row_description_and_full_export_columns():
    context = application_context()
    rows = build_rows(context[0], context[1], [])[0]
    assert rows[0]['source_row'] == 257 and rows[0]['source_row_id'] == 'R'
    compact = control.columns()
    audit = control.columns(audit=True)
    assert len([column for column in compact if not column.get('hidden')]) == 9
    assert {column['fieldname'] for column in audit} <= {column['fieldname'] for column in compact}
    assert next(column for column in compact if column['fieldname'] == 'description')['hidden'] == 1
    assert next(column for column in audit if column['fieldname'] == 'source_row')['label'] == 'Fila original'
