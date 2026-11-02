"""Real reconciliation journals and presentation-only migration; always rollback.

The runner applies schema before fixtures. Only that already-applied schema reload
and asynchronous notifications are suppressed; the financial flow uses real SQL,
controllers, allocation journals and PeriodTotalsContext without financial mocks.
"""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import credit_application
from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.period_totals import PeriodTotalsContext
from credinomina_reconciliation.patches.v1_0 import backfill_period_cash_breakdown as migration
from credinomina_reconciliation.patches.v1_0 import backfill_period_financial_totals, backfill_period_coverage_components
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def _fixture(marker, amounts, memberships):
    employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
        employer_code=marker, payroll_frequency='Mensual')).insert()
    periods = [frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
        payroll_month=f'2025-{month:02d}-01', reconciliation_mode='Historica',
        historical_scope='Fecha exacta', historical_application_date=f'2025-{month:02d}-30')).insert()
        for month in range(4, 5 + max(memberships))]
    imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
        currency='USD', status='Importado', source_file=f'/private/files/{marker}.csv'))
    for index, (amount, membership) in enumerate(zip(amounts, memberships)):
        period = periods[membership]
        imported.append('rows', dict(source_key=f'{marker}-{index}', source_row=index + 2,
            event_type='Aplicacion', event_date=period.historical_application_date, currency='USD',
            amount=amount, amount_usd=amount, effective=1, client_number=f'{marker}-{index}',
            client_name=f'Cliente de ensayo {index}', loan_number=f'{80000 + index}-1',
            historical_period=period.name, processing_route='Historica'))
    imported.insert()
    _reconcile_sources(employer.name)
    imported.reload()
    deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
        deposit_reference=marker, deposit_date='2025-06-01', deposit_currency='USD', deposit_amount=90)).insert()
    deposit.submit()
    with imported_item(marker):
        complement = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
            category='Ajuste de conciliación', subcategory='Otro ajuste sin CxC',
            posting_date='2025-06-01', currency='USD', amount=-10, reference=marker,
            review_action='Partida de depósito', amount_reviewed=1,
            description='Contrapartida negativa importada del core', accounting_source_key=marker,
            source_account='160209013004', source_currency='USD', source_debit=0, source_credit=10,
            source_date='2025-06-01', source_file=imported.source_file, source_row=20,
            source_file_hash=marker, source_voucher=marker, voucher=marker, voucher_line=marker,
            accounting_classification='Movimiento interno')).insert()
    complement.submit()
    deposit.reload()
    deposit.append('targets', dict(complementary_item=complement.name, amount_usd=-10))
    for row, amount in zip(imported.rows, amounts):
        deposit.append('targets', dict(historical_application=row.name, amount_usd=amount))
    deposit.save()
    reconcile_deposit(deposit)
    deposit.reload(); imported.reload()
    for period in periods:
        period.reload()
    assert deposit.result == 'Conciliado' and deposit.allocated_usd == 90, deposit.as_dict()
    journal = json.loads(deposit.allocation_detail)
    assert sum(row['importe_usd'] for row in journal) == 90, journal
    assert sorted(row['importe_usd'] for row in journal if row['tipo'] == 'Aplicacion historica') == sorted(amounts), journal
    assert [row['importe_usd'] for row in journal if row['tipo'] == 'Partida complementaria'] == [-10], journal
    # The gross application evidence remains unchanged: the negative adjustment
    # belongs to the complete deposit, without guessed portions for each loan.
    assert [row.historical_remitted_usd for row in imported.rows] == amounts
    assert [row.amount_usd for row in imported.rows] == amounts
    return employer, periods, imported, complement, deposit


def _without_breakdown(period):
    return {field: value for field, value in period.as_dict().items() if field not in migration.FIELDS}


def _credit_fixture(marker):
    employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
    client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
        client_name=marker, client_number=marker)).insert()
    origin = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
        deposit_reference=marker + '-ORIGIN', deposit_date='2025-04-01',
        deposit_currency='USD', deposit_amount=10)).insert()
    origin.submit()
    with imported_item(marker):
        credit = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
            category='Saldo a favor del cliente', posting_date='2025-04-01', currency='USD', amount=10,
            registered_deposit=origin.name, credit_client=client.name,
            credit_assigned_to='Administrator', credit_commitment_date='2025-06-30',
            credit_treatment='Aplicación futura', description='Saldo de un depósito anterior',
            accounting_source_key=marker, source_account='160209013004', source_currency='USD',
            source_debit=0, source_credit=10, source_date='2025-04-01',
            source_file=f'/private/files/{marker}-credit.csv', source_row=2, source_file_hash=marker,
            source_voucher=marker, voucher=marker, voucher_line=marker,
            accounting_classification='Movimiento interno')).insert()
    credit.submit()
    period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
        payroll_month='2025-05-01', reconciliation_mode='Historica', historical_scope='Fecha exacta',
        historical_application_date='2025-05-30')).insert()
    imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
        currency='USD', status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(
            source_key=marker, source_row=2, event_type='Aplicacion', event_date='2025-05-30',
            currency='USD', amount=100, amount_usd=100, effective=1, client=client.name,
            client_number=client.client_number, client_name=client.client_name, loan_number='90001-1',
            historical_period=period.name, processing_route='Historica')])).insert()
    _reconcile_sources(employer.name)
    imported.reload()
    deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
        deposit_reference=marker + '-CURRENT', deposit_date='2025-06-01', deposit_currency='USD',
        deposit_amount=90, targets=[dict(historical_application=imported.rows[0].name, amount_usd=90)])).insert()
    deposit.submit(); reconcile_deposit(deposit)
    deposit.reload(); origin.reload(); credit.reload()
    origin_state, cash_journal = origin.as_dict(), deposit.allocation_detail
    credit_application.apply(credit.name, str(credit.modified), imported.rows[0].name, 10, '2025-06-01')
    imported.reload(); deposit.reload(); origin.reload(); period.reload(); credit.reload()
    assert origin.as_dict() == origin_state and deposit.allocation_detail == cash_journal
    assert json.loads(deposit.allocation_detail)[0]['importe_usd'] == 90
    assert imported.rows[0].historical_remitted_usd == 100
    funding = [entry for entry in json.loads(imported.rows[0].historical_detail) if entry.get('saldo_a_favor')]
    assert len(funding) == 1 and funding[0]['importe_usd'] == 10, funding
    return (employer, [period], imported, credit, deposit), origin


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Client', 'CN Reconciliation Period', 'CN Accounting Import', 'CN Source Row',
        'CN Remittance Allocation', 'CN Remittance Target', 'CN Complementary Item')
    before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            seed_subcategories()
            fixtures = [_fixture('CASH-BREAKDOWN-' + label + frappe.generate_hash(length=8), amounts, memberships)
                for label, amounts, memberships in [('ONE-', [100], [0]), ('SAME-', [40, 60], [0, 0]),
                                                     ('SHARED-', [40, 60], [0, 1])]]
            credit_fixture, credit_origin = _credit_fixture('CASH-CREDIT-' + frappe.generate_hash(length=8))
            fixtures.append(credit_fixture)
            periods = [period for _employer, group, *_rest in fixtures for period in group]
            expected = {}
            for index, (_employer, group, imported, _complement, deposit) in enumerate(fixtures):
                journal = deposit.allocation_detail
                source_rows = [row.as_dict() for row in imported.rows]
                context = PeriodTotalsContext(group)
                for period in group:
                    values = context.values(period)
                    expected[period.name] = values
                    assert values['pending_usd'] == 0, values
                    if index < 2:
                        assert (values['cash_breakdown_known'], values['deposit_coverage_usd'],
                            values['complementary_coverage_usd'], values['negative_complementary_usd'],
                            values['remitted_total_usd']) == (1, 90, 10, -10, 100), values
                    elif index == 2:
                        assert values['cash_breakdown_known'] == 0 and values['cash_breakdown_note'], values
                        assert all(values[field] is None for field in migration.FIELDS[:3]), values
                        assert values['remitted_total_usd'] in (40, 60), values
                        period.recalculate_totals()
                        assert period.cash_breakdown_known == 0
                        assert all(period.get(field) is None for field in migration.FIELDS[:3])
                        period.save(); period.reload()
                        assert period.cash_breakdown_known == 0 and period.cash_breakdown_note
                        assert all(period.get(field) == 0 for field in migration.FIELDS[:3])
                    else:
                        assert (values['cash_breakdown_known'], values['deposit_coverage_usd'],
                            values['complementary_coverage_usd'], values['negative_complementary_usd'],
                            values['remitted_total_usd']) == (1, 90, 10, 0, 100), values
                deposit.reload(); imported.reload()
                assert deposit.allocation_detail == journal
                assert [row.as_dict() for row in imported.rows] == source_rows
            # Rebuild old presentation fields on closed records. Keep the real
            # balances, source links and executed journals frozen throughout.
            for period in periods:
                frappe.db.set_value(period.doctype, period.name, dict(status='Cerrado',
                    deposit_coverage_usd=123, complementary_coverage_usd=456,
                    negative_complementary_usd=789, cash_breakdown_known=0,
                    cash_breakdown_note='Desglose anterior'), update_modified=False)
                period.reload()
            period_states = {period.name: _without_breakdown(period) for period in periods}
            documents = [doc for _employer, _group, *docs in fixtures for doc in docs]
            documents.append(credit_origin)
            states = {(doc.doctype, doc.name): doc.reload().as_dict() for doc in documents}
            original_get_all = frappe.get_all
            employer_names = [employer.name for employer, *_rest in fixtures]

            def fixture_periods(doctype, *args, **kwargs):
                # Scope the migration's scan to rollback-only fixture companies;
                # keep its real database query and every financial query intact.
                if doctype == 'CN Reconciliation Period' and kwargs.get('pluck') == 'name':
                    kwargs['filters'] = dict(kwargs.get('filters') or {}, employer=['in', employer_names])
                return original_get_all(doctype, *args, **kwargs)

            with patch.object(frappe, 'reload_doc'), patch.object(frappe, 'get_all', side_effect=fixture_periods):
                # An installation upgrading from an older release must also
                # survive the preceding patches when cash is indeterminate.
                backfill_period_financial_totals.execute()
                backfill_period_coverage_components.execute()
                migration.execute()
                migration.execute()
            for period in periods:
                period.reload()
                assert _without_breakdown(period) == period_states[period.name], period.as_dict()
                for field in migration.FIELDS:
                    actual, wanted = period.get(field), expected[period.name][field]
                    if wanted is None and field.endswith('_usd'):
                        wanted = 0  # NOT NULL storage; flag=0 retains the unknown meaning.
                    assert actual == wanted, (period.name, field, actual, wanted)
            stale = periods[0]
            frappe.db.set_value(stale.doctype, stale.name,
                dict(remitted_total_usd=90, pending_usd=10), update_modified=False)
            stale.reload()
            stale_state = _without_breakdown(stale)
            with patch.object(frappe, 'reload_doc'), patch.object(frappe, 'get_all', side_effect=fixture_periods):
                migration.execute()
            stale.reload()
            assert _without_breakdown(stale) == stale_state
            assert stale.cash_breakdown_known == 0 and 'totales guardados' in stale.cash_breakdown_note
            assert all(stale.get(field) == 0 for field in migration.FIELDS[:3])
            for doc in documents:
                assert doc.reload().as_dict() == states[(doc.doctype, doc.name)], (doc.doctype, doc.name)
            return dict(single_application_cash_usd=90, negative_complementary_usd=-10,
                complementary_coverage_usd=10, same_period_without_proration=True,
                separate_credit_origin_funding_excluded_from_bank_cash=True,
                shared_periods_unknown=True, closed_patch_preserves_balances_links_journals=True,
                stale_closed_balances_preserved_with_unknown_breakdown=True,
                older_pending_patches_accept_unknown_components=True,
                idempotent_patch=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {doctype: frappe.db.count(doctype) for doctype in doctypes}, 'El ensayo dejó registros'
