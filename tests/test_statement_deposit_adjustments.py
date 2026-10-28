from copy import deepcopy
from unittest.mock import patch

from credinomina_reconciliation import company_accounting_statement as accounting
from credinomina_reconciliation.statement_deposit_adjustments import project, ADJUSTMENT_FIELD, CALCULATED_FIELD
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


def bank_line(name, original, bank, **extra):
    return dict(employer='ACEREAL', statement_scope=accounting.CORE, core_balance_usd=-original,
        core_credit_usd=original, core_balance_nio=0, source_currency='USD', source_document=name,
        source_doctype='CN Remittance Allocation', remittance_allocation=name,
        _bank_deposit=dict(name=name, employer='ACEREAL', docstatus=1, amount_usd=bank,
            deposit_date='2026-05-15', deposit_currency='USD', **extra))


def examples():
    lines = [bank_line('DEP-9', 3569.99, 3598.70), bank_line('DEP-5', 3709.78, 3770.04),
        bank_line('DEP-4', 3593.02, 3593.00, deposit_amount=131591.02, fx_rate=36.6243)]
    lines[-1]['_bank_deposit']['deposit_currency'] = 'NIO'
    lines[-1].update(core_balance_nio=-131591.02, source_currency='NIO', fx_rate=36.6243)
    lines.append(dict(employer='ACEREAL', statement_scope=accounting.CORE, core_balance_usd=11559.11,
        core_debit_usd=11559.11, source_document='IMPORT', source_doctype='CN Accounting Import'))
    return lines


def management():
    return [dict(employer='ACEREAL', source_document='APP', source_doctype='CN Accounting Import',
        balance_usd=1357.58, application_pending_usd=1357.58, _component='application_pending_usd'),
        dict(employer='ACEREAL', source_document='DEP', source_doctype='CN Remittance Allocation',
        balance_usd=-760.21, deposit_pending_usd=-760.21, _component='deposit_pending_usd')]


def test_three_actual_adjustments_explain_8895_without_moving_management_or_core():
    core = examples()
    before = deepcopy(core)
    adjustments = project(core, '2026-08-31')
    assert [row[ADJUSTMENT_FIELD] for row in adjustments] == [28.71, 60.26, -.02]
    assert adjustments[-1]['status'] == 'Redondeo verificado'
    assert adjustments[-1]['accounting_status'] == 'No requiere registro'
    assert adjustments[0]['status'] == 'Pendiente de documentar'
    total = accounting.summarize([*core, *accounting.management_detail(management()), *adjustments])[0]
    assert total['management_net_usd'] == 597.37
    assert total[ADJUSTMENT_FIELD] == 88.95
    assert total[CALCULATED_FIELD] == total['core_balance_usd'] == 686.32
    assert total['reconciliation_difference_usd'] == 0
    assert all(row['management_amount_usd'] == row['company_receivable_usd'] == row['employee_receivable_usd'] == 0 for row in adjustments)
    assert core == before


def test_unexplained_difference_is_not_used_as_an_automatic_balancing_adjustment():
    core = examples()
    core[-1]['core_balance_usd'] += .08
    total = accounting.summarize([*core, *accounting.management_detail(management()), *project(core)])[0]
    assert total[ADJUSTMENT_FIELD] == 88.95
    assert total['reconciliation_difference_usd'] == -.08


def test_unknown_conversion_cannot_become_zero_and_cancelled_future_linked_origins_are_excluded():
    core = examples()[:1]
    for field, value in [('docstatus', 2), ('cash_identity_status', 'Origen vinculado'), ('deposit_date', '2026-09-01')]:
        changed = deepcopy(core)
        changed[0]['_bank_deposit'][field] = value
        assert project(changed, '2026-08-31') == []
    core[0]['core_balance_usd'] = None
    adjustments = project(core)
    assert adjustments[0][ADJUSTMENT_FIELD] is None
    total = accounting.summarize([*core, *adjustments])[0]
    assert total[CALCULATED_FIELD] is None and total['reconciliation_difference_usd'] is None


def test_summary_order_and_both_new_drills_preserve_actual_amounts():
    with patch.object(report, 'load_core_detail', return_value=examples()), \
            patch.object(report, 'load_detail', side_effect=lambda filters: deepcopy(management())), \
            patch.object(report, 'load_deposit_differences', return_value=[]):
        columns, summary, _ = report.execute({'compact_view': 1})
        _, actual, _ = report.execute(dict(view_mode='Detalle'))
        _, adjusted, _ = report.execute(dict(view_mode='Detalle', component=CALCULATED_FIELD))
        _, pending, _ = report.execute(dict(view_mode='Detalle', component=ADJUSTMENT_FIELD))
        adjustment_summary = report.execute(dict(component=ADJUSTMENT_FIELD))[1][0]
    assert summary[0][CALCULATED_FIELD] == 686.32
    assert adjustment_summary[CALCULATED_FIELD] == adjustment_summary['core_balance_usd'] == 686.32
    visible = [column['fieldname'] for column in columns if not column.get('hidden')]
    index = visible.index('management_net_usd')
    assert visible[index:index+5] == ['management_net_usd', ADJUSTMENT_FIELD, CALCULATED_FIELD, 'core_balance_usd', 'reconciliation_difference_usd']
    assert actual[-1]['statement_amount_usd'] == 597.37
    assert adjusted[-1]['statement_amount_usd'] == 686.32
    assert pending[-1]['statement_amount_usd'] == 88.95
    assert len(pending) == 4
    assert round(sum(row['statement_amount_usd'] for row in adjusted[:-1]), 2) == adjusted[-1]['statement_amount_usd']
    assert ADJUSTMENT_FIELD not in {column['fieldname'] for column in report.get_columns({'status': 'Pendiente'})}


def test_calculated_balance_rejects_filters_that_change_only_one_population():
    import frappe
    import pytest
    with pytest.raises(frappe.ValidationError, match='alcance completo'):
        report.execute(dict(component=CALCULATED_FIELD, search='Un cliente'))
