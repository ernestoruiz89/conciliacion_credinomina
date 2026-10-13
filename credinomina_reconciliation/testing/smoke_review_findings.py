"""SQL regression of review fixes. Isolated fixtures, always rolled back."""
from unittest.mock import patch
import frappe


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture, new_deposit, credit_item
    from credinomina_reconciliation import historical_cutoff
    from credinomina_reconciliation.receivable_recovery import search_recovery_destinations, get_recovery_destination
    from credinomina_reconciliation.control_summary import collection_summaries
    from credinomina_reconciliation.company_statement import load_detail, summarize
    try:
        frappe.set_user('Administrator')
        marker = 'REVIEW-' + frappe.generate_hash(length=8)
        company, period, imported, origin, original = fixture(marker)
        cash = new_deposit(company.name, marker + '-CASH', 25)
        counterpart = credit_item(company.name, 7, marker)
        for method, doctype, expected in [('Depósito', 'CN Remittance Allocation', cash.name),
                                          ('Compensación', 'CN Complementary Item', counterpart.name)]:
            result = search_recovery_destinations(doctype, '', 'name', 0, 20,
                dict(item_name=origin.name, employer=company.name, method=method))
            assert [row[0] for row in result] == [expected], result
            assert get_recovery_destination(origin.name, company.name, method, expected)['available_usd'] > 0

        # Permission-scoped query boundary: same SQL evidence, one hidden parent.
        normal_records = historical_cutoff.records
        def restricted(doctype, **kwargs):
            return [row for row in normal_records(doctype, **kwargs)
                    if doctype != 'CN Remittance Allocation' or row.name != original.name]
        with patch.object(historical_cutoff, 'records', side_effect=restricted):
            try:
                historical_cutoff.load_cutoff({'cutoff_date':'2025-05-31', 'employer':company.name})
            except frappe.PermissionError as error:
                assert original.name not in str(error) and 'Saldo no determinable' in str(error)
            else:
                raise AssertionError('A hidden deposit reopened paid debt')

        # Deliberately inconsistent projections exist only within this transaction.
        frappe.db.set_value('CN Remittance Allocation', cash.name,
            dict(allocated_usd=25, result='Revisar detalle', detail_status='Revisar filas'), update_modified=False)
        detail = load_detail(dict(employer=company.name, component='deposit_pending_usd'))
        row = next(row for row in detail if row['source_document'] == cash.name)
        assert row['balance_usd'] == 0 and row['status'] == 'Revisar detalle'
        frappe.db.set_value('CN Remittance Allocation', cash.name, 'allocated_usd', 30, update_modified=False)
        detail = load_detail(dict(employer=company.name, component='deposit_pending_usd'))
        row = next(row for row in detail if row['source_document'] == cash.name)
        assert row['balance_usd'] is None and row['observed_usd'] == 5
        assert summarize(detail)[0]['balance_usd'] is None

        # Synthetic child for the SQL aggregate only; no reconciliation is run.
        frappe.get_doc(dict(doctype='CN Collection Row', name=marker + '-ROW',
            parent=period.name, parenttype='CN Reconciliation Period', parentfield='collection_rows', idx=1,
            client_name=marker, expected_usd=0, expected_nio=3662.43, applied_usd=100,
            application_status='Aplicado y remitido')).db_insert()
        totals = collection_summaries([period.name])[period.name]
        assert totals.quality_missing_basis_count == 1 and totals.worker_gap_usd == 0
        return dict(hidden_evidence='blocked without disclosure', selectors=2,
            zero_balance_review='visible', invalid_distribution='unknown, not debt', nio_conversion='counted separately')
    finally:
        frappe.db.rollback()
