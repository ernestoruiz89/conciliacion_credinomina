from copy import deepcopy
import json
from unittest.mock import patch

from credinomina_reconciliation.reconciliation_client_detail import consolidate
from credinomina_reconciliation.reconciliation_report import build_views
from credinomina_reconciliation.reconciliation_pending_detail import _line
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as report


def application_rows():
    periods = [dict(name=name, employer='E', payroll_month=month, reconciliation_mode='Historica', applied_usd=20)
        for name, month in (('P1', '2026-07-01'), ('P2', '2026-08-01'))]
    apps = [dict(name=name, historical_period=period, event_type='Aplicacion', client_number='00123',
        client_name='Juan', loan_number=loan, currency='USD', amount=20, historical_remitted_usd=paid,
        historical_detail=json.dumps([dict(importe_usd=paid)]))
        for name, period, loan, paid in (('A1', 'P1', '0012-1', 10), ('A2', 'P2', '0013-1', 30))]
    details = build_views(periods, [], apps)[1]
    for row in details:
        row['portfolio_credit_status'] = 'Corriente'
    return details


def pending(amount=20, complementary=False, **changes):
    source = dict(dict(employer='E', client_number='123', client_name='Juan', event_date='2026-08-31',
        source_doctype='CN Complementary Item' if complementary else 'CN Remittance Allocation',
        source_document='COMP' if complementary else 'DEP', loan_number='0012-1'), **changes)
    return _line(source, {}, amount, complementary=complementary)


def test_consolidates_credits_and_periods_without_netting_claims_or_pending_items():
    details = [*application_rows(), pending(), pending(5, True), pending(-5, True)]
    before = deepcopy(details)
    rows = consolidate(details)
    assert len(rows) == 1
    row = rows[0]
    assert row['application_usd'] == row['deposit_usd'] == 40
    assert row['open_usd'] == 10 and row['deposit_difference_usd'] == 0
    assert row['deposit_unallocated_usd'] == 20 and row['complementary_unallocated_usd'] == 10
    assert row['cash_adjustment_usd'] == 0 and row['credit_used_usd'] == 0
    assert row['name'] == 'P1 · P2' and row['loan_number'] == '0012-1 · 0013-1'
    assert row['payroll_month'] == '2026-07 · 2026-08'
    assert row['cash_control'] == 'Revisar: cobertura y asignación pendientes'
    assert '+5.00' in row['observations'] and '-5.00' in row['observations']
    assert details == before


def test_unassigned_only_client_is_visible_without_inventing_application_or_collection():
    row = consolidate([pending(7), pending(-3, True)])[0]
    assert row['deposit_usd'] == row['application_usd'] == row['open_usd'] == 0
    assert row['collection_usd'] is None and row['collection_control'] == 'No aplica'
    assert row['deposit_unallocated_usd'] == 7 and row['complementary_unallocated_usd'] == 3
    assert row['cash_control'] == 'Revisar: pendiente de aplicar' and row['has_difference']


def test_ambiguous_names_and_unattributed_cash_are_not_assigned_to_a_client():
    first = application_rows()[0]
    second = dict(first, client_number='456', loan_number='14-1')
    ambiguous = pending(client_number='', loan_number='')
    company = pending(client_number='', client_name='Sin cliente individual', loan_number='')
    rows = consolidate([first, second, ambiguous, company])
    assert len(rows) == 4
    assert all(row['deposit_unallocated_usd'] == 0 for row in rows if row['client_number'])
    assert sum(row['deposit_unallocated_usd'] for row in rows) == 40
    assert any('sin identificador único' in row['observations'] for row in rows)


def test_unique_exact_name_or_credit_can_join_a_pending_item_to_its_identified_client():
    base = application_rows()
    assert len(consolidate([*base, pending(client_number='', loan_number='0012-1')])) == 1
    assert len(consolidate([*base, pending(client_number='', loan_number='')])) == 1
    other = dict(base[0], client_number='456', client_name='Otro cliente', loan_number='14-1')
    conflict = pending(client_number='', client_name='Otro cliente', loan_number='12-1')
    assert len(consolidate([base[0], other, conflict])) == 3


def test_same_identified_client_spans_employers_without_absorbing_company_differences():
    base = application_rows()[0]
    other = dict(base, employer='OTHER')
    company_difference = dict(base, _company_conversion=True, client_number='', client_name='E · Diferencia documentada de empresa',
        application_usd=0, deposit_usd=-.01, conversion_usd=.01, open_usd=0)
    rows = consolidate([base, other, company_difference])
    assert len(rows) == 2
    client = next(row for row in rows if row['client_number'])
    assert client['employer'] == 'E · OTHER'
    assert client['deposit_usd'] == 20 and client['conversion_usd'] == 0
    assert 'Empresa E;' in client['observations'] and 'Empresa OTHER;' in client['observations']


def test_national_id_does_not_become_another_persons_client_number():
    p = dict(name='P', employer='E', reconciliation_mode='Historica', applied_usd=20)
    base = dict(historical_period='P', currency='USD', amount=10, loan_number='12-1')
    sources = [dict(base, name='A', client_number='123', national_id='555', client_name='Ana'),
               dict(base, name='B', national_id='123', client_name='Beatriz')]
    _, detail = build_views([p], [], sources)
    assert [(r['client_name'], r['client_number']) for r in detail] == [('Ana', '123'), ('Beatriz', '')]
    rows = consolidate([*detail, pending(2, client_number='', client_name='Ana', national_id='555')])
    assert [(r['application_usd'], r['deposit_unallocated_usd']) for r in rows] == [(10, 2), (10, 0)]


def test_collection_variance_does_not_change_covered_cash_status():
    claim = dict(name='C', parent='P', client='1', client_name='Ana', expected_usd=30, applied_usd=20,
                 remitted_usd=20, remittance_detail='[{"importe_usd":20}]')
    period = dict(name='P', employer='E', applied_usd=20)
    row, = consolidate(build_views([period], [claim], [])[1])
    assert row['has_difference'] and row['collection_control'] == 'Con diferencias'
    assert row['cash_control'] == 'Cubierta' and row['open_usd'] == 0


def test_unknown_pending_does_not_poison_assigned_cash_or_become_zero():
    row = consolidate([*application_rows(), pending(None), pending(None, True)])[0]
    assert row['deposit_usd'] == 40 and row['open_usd'] == 10
    assert row['deposit_unallocated_usd'] is None and row['complementary_unallocated_usd'] is None
    assert row['cash_control'] == 'Revisar: importes sin determinar'


def test_collection_no_aplica_is_not_confused_with_unknown_collection_and_text_is_preserved():
    rows = application_rows()
    rows[0].update(collection_usd=25, collection_difference_usd=5, collection_control='Con diferencias', comments='Comentarios: Documentado')
    rows[1].update(comments='Comentarios: Documentado', portfolio_credit_status='Cancelado')
    row = consolidate(rows)[0]
    assert row['collection_usd'] == 25 and row['collection_difference_usd'] == 5
    assert row['collection_control'] == 'Con diferencias' and row['comments'] == 'Comentarios: Documentado'
    assert '12-1 · 2026-07: Corriente' in row['portfolio_credit_status']
    assert '13-1 · 2026-08: Cancelado' in row['portfolio_credit_status']
    rows[0]['collection_usd'] = None
    assert consolidate(rows)[0]['collection_usd'] is None


def execute(extra=None, pending_rows=None):
    with patch.object(report, 'records', return_value=[]), patch.object(report, 'child_records', return_value=[]), \
            patch.object(report, 'load_direct_applications', return_value=[]), \
            patch.object(report, 'build_views', return_value=([], application_rows())), \
            patch.object(report, 'add_portfolio_status'), \
            patch.object(report, 'load_pending_detail', return_value=[pending()] if pending_rows is None else pending_rows):
        return report.execute(dict(view='Detalle por Cliente', **(extra or {})))


def test_month_period_checkboxes_show_gathered_values_without_changing_client_grouping():
    for month, period in ((0, 0), (1, 0), (0, 1), (1, 1)):
        columns, rows, *_ = execute(dict(show_month=month, show_period=period, only_differences=1))
        fields = {c['fieldname']: c for c in columns}
        assert len(rows) == 1 and rows[0]['application_usd'] == 40
        assert ('payroll_month' in fields) == bool(month) and ('name' in fields) == bool(period)
        for field, enabled in (('payroll_month', month), ('name', period)):
            if enabled:
                assert fields[field]['fieldtype'] == 'Data'  # Multiple values cannot be a Date or one Link.
        assert fields['deposit_usd']['label'] == 'Depósito asignado US$'
        assert {'deposit_unallocated_usd', 'complementary_unallocated_usd'} <= fields.keys()


def test_unknown_new_columns_disable_totals_but_separate_known_columns_allow_them():
    assert execute()[5] is False
    result = execute(pending_rows=[pending(None)])
    assert result[5] is True and 'no determinables' in result[2]


def test_existing_report_views_keep_their_columns_regardless_of_client_checkboxes():
    for detail in (False, True):
        columns = report.get_columns(detail, show_month=True, show_period=True)
        assert {'name', 'payroll_month'} <= {c['fieldname'] for c in columns}
        assert not {'deposit_unallocated_usd', 'complementary_unallocated_usd'} & {c['fieldname'] for c in columns}
