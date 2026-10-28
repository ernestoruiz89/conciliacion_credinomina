from copy import deepcopy
from unittest.mock import patch

import pytest

from credinomina_reconciliation import difference_reasons as reasons
from credinomina_reconciliation.company_statement import FIELDS
from credinomina_reconciliation.company_accounting_statement import CORE, MANAGEMENT, summarize, matches
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


CATALOG = [dict(name='Cliente', treatment='A Favor Cliente'), dict(name='Empresa', treatment='A Favor Empresa'),
           dict(name='Sin clasificar', treatment='')]


def position(name, component, amount, reason):
    return dict(employer='E', source_document=name, source_doctype='CN Complementary Item',
        difference_reason=reason, event_date='2025-05-01', status='Pendiente', _component=component,
        balance_usd=amount, **{field: amount if field == component else 0 for field in FIELDS})


def test_classification_moves_signed_credit_once_and_preserves_receivables_and_core():
    from credinomina_reconciliation.company_accounting_statement import management_detail
    rows = management_detail([position('C', 'core_pending_usd', -10, 'Cliente'),
        position('D', 'deposit_pending_usd', -20, 'Empresa'),
        position('R', 'application_pending_usd', 7, 'Cliente'),
        position('U', 'core_pending_usd', -5, 'Sin clasificar')])
    core = dict(position('CORE', 'core_pending_usd', -100, 'Cliente'), statement_scope=CORE,
                core_balance_usd=-100, management_amount_usd=0)
    rows.append(core)
    with patch('credinomina_reconciliation.report_records.records', return_value=CATALOG) as lookup:
        reasons.attach_treatments(rows, classify_credits=True)
    lookup.assert_called_once()
    assert rows[0]['client_credit_usd'] == -10 and rows[0]['core_pending_usd'] == 0
    assert rows[1]['company_credit_usd'] == -20 and rows[1]['deposit_pending_usd'] == 0
    assert rows[2]['application_pending_usd'] == 7 and rows[2]['client_credit_usd'] == 0
    assert rows[3]['core_pending_usd'] == -5 and rows[3]['difference_treatment'] == ''
    assert core['core_balance_usd'] == -100 and core['core_pending_usd'] == -100
    totals = summarize(rows[:-1])[0]
    assert totals['management_net_usd'] == -28
    assert totals['other_credit_usd'] == -5
    assert totals['management_pending_usd'] == 7
    assert matches(rows[0], {'component': 'client_credit_usd'})
    assert not matches(rows[0], {'component': 'core_pending_usd'})
    assert matches(rows[0], {'difference_treatment': 'A Favor Cliente'})
    assert not matches(rows[1], {'difference_treatment': 'A Favor Cliente'})


def test_blank_unreadable_mixed_and_documentary_reasons_never_invent_credit():
    from credinomina_reconciliation.company_accounting_statement import management_detail
    rows = management_detail([position(str(i), 'core_pending_usd', -10, reason) for i, reason in enumerate(
        ('', 'No visible', 'Cliente · Empresa', 'Cliente · Sin clasificar'))])
    documentary = dict(position('DIFF', None, 0, 'Empresa'), statement_scope=MANAGEMENT,
                       management_amount_usd=0, pending_core_difference_usd=-.01)
    rows.append(documentary)
    with patch('credinomina_reconciliation.report_records.records', return_value=CATALOG):
        reasons.attach_treatments(rows, classify_credits=True)
    assert all(row['_component'] == 'core_pending_usd' and row['core_pending_usd'] == -10 for row in rows[:4])
    assert rows[2]['difference_treatment'] == 'A Favor Cliente · A Favor Empresa'
    assert documentary['company_credit_usd'] == 0 and documentary['pending_core_difference_usd'] == -.01
    with patch('credinomina_reconciliation.report_records.records') as lookup:
        reasons.attach_treatments([{'difference_reason': ''}], classify_credits=True)
    lookup.assert_not_called()


@pytest.mark.parametrize('historical', [False, True])
def test_summary_and_credit_drill_use_same_population_after_classification(historical):
    original = [position('C', 'core_pending_usd', -10, 'Cliente'),
                position('D', 'deposit_pending_usd', -20, 'Empresa')]
    saved = deepcopy(original)
    filters = {'view_mode': 'Resumen', 'statement_scope': MANAGEMENT}
    if historical:
        filters['cutoff_date'] = '2025-06-30'
    with patch.object(report, 'load_detail', return_value=original) as current, \
            patch.object(report, 'load_deposit_differences', return_value=[]), \
            patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=dict(detail=original, warnings=[])) as cutoff, \
            patch('credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'), \
            patch('credinomina_reconciliation.report_records.records', return_value=CATALOG):
        summary = report.execute(filters)[1][0]
        detail = report.execute(dict(filters, view_mode='Detalle', component='client_credit_usd'))[1]
    assert summary['client_credit_usd'] == -10
    assert summary['company_credit_usd'] == -20
    assert summary['management_net_usd'] == -30
    assert [row['source_document'] for row in detail if not row.get('_is_statement_total')] == ['C']
    assert detail[-1]['statement_amount_usd'] == -10
    assert 'component' not in (cutoff if historical else current).call_args.args[0]
    assert original == saved


def test_report_exposes_classification_in_compact_detail():
    columns = {column['fieldname']: column for column in report.get_columns(dict(view_mode='Detalle', compact_view=1))}
    assert not columns['difference_treatment'].get('hidden')


def test_distributed_credit_retains_source_reason_for_statement_classification():
    import json
    from credinomina_reconciliation.reclassification_distribution import deposit_credits, DEPOSIT
    from credinomina_reconciliation.company_statement import build_detail
    from credinomina_reconciliation.company_accounting_statement import management_detail
    source = dict(name='CORE', difference_reason='Empresa', reclassification_allocations=json.dumps([
        dict(operation_id='P', kind=DEPOSIT, parent_document='DEP', target='R', employer='E', client='C',
             credit_origin_date='2025-05-01', effective_date='2025-06-01', amount_usd=10)]))
    credits = deposit_credits([source], {'DEP'})
    rows = management_detail(build_detail([], [], credits, []))
    with patch('credinomina_reconciliation.report_records.records', return_value=CATALOG):
        reasons.attach_treatments(rows, classify_credits=True)
    assert rows[0]['difference_reason'] == 'Empresa'
    assert rows[0]['company_credit_usd'] == -10 and rows[0]['client_credit_usd'] == 0
    assert summarize(rows)[0]['management_net_usd'] == -10
