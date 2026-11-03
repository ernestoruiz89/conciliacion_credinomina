"""Reproduce the 490.53 detail / 490.52 deposit without personal source data."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_difference import record, KIND
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.difference_reasons import seed_reasons
from credinomina_reconciliation.immaterial_differences import REASON
from credinomina_reconciliation.rounding import money, sum_money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail


def run(native=False, repair_existing=False, closed=False):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            seed_reasons()
            amounts = [31.90, 21.13, 10.77, 20.23, 11.44, 26.45, 10.37, 21.95, 29.48,
                       22.10, 21.96, 21.95, 64.62, 24.20, 32.23, 93.13, 26.62]
            marker = "SHORT-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2026-03-01", reconciliation_mode="Historica")).insert()
            records, details = [], []
            native_amounts = [1168.31, 773.97, 394.62, 740.74, 418.84, 968.76, 379.71,
                              803.82, 1079.78, 809.36, 804.20, 803.85, 2366.72, 886.16,
                              1180.40, 3410.78, 974.81]
            for index, amount in enumerate(amounts):
                identity = f"{marker}-{index}"
                client = frappe.get_doc(dict(doctype="CN Client", employer=employer.name,
                    client_number=identity, client_name=identity)).insert()
                records.append(dict(source_key=identity, event_type="Aplicacion", event_date="2026-03-12",
                    currency="USD", amount=amount, amount_usd=amount, effective=1, client=client.name,
                    client_number=identity, client_name=identity, loan_number=identity + "-1",
                    historical_period=period.name, processing_route="Historica"))
                if native:
                    records[-1].update(equivalent_currency="NIO", equivalent_amount=native_amounts[index], source_fx_rate=36.6243)
                details.append(dict(source_row=index + 2, client_number=identity, client_name=identity,
                    loan_number=identity + "-1", deducted_usd=amount))
            imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD", rows=records)).insert()
            _reconcile_sources(employer.name)
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_reference=marker, deposit_date="2026-03-13", deposit_currency="NIO",
                deposit_amount=17964.90, fx_rate=36.6243, apply_fifo=1,
                detail_periods=[dict(period=period.name)], detail_file=f"/private/files/{marker}.xlsx")).insert()
            _apply_remittance_detail(deposit, details, marker.encode(), deposit.detail_file)
            deposit.reload(); deposit.submit(); deposit.reload()
            assert money(deposit.amount_usd) == money(490.52)
            assert money(deposit.detail_total_usd) == money(490.53)
            record(deposit.name, str(deposit.modified), REASON, "Inmaterial")
            deposit.reload()
            other = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_reference=marker + "-OTHER", deposit_date="2026-03-14", deposit_currency="USD",
                deposit_amount=5)).insert()
            other.submit(); other.reload()
            other_before = frappe.as_json(other.as_dict())
            source_ids = [row.name for row in imported.rows]
            if native or repair_existing:
                # Reproduce the already-saved partial assignment from the old
                # implementation, before keeping the difference on the company.
                with patch('credinomina_reconciliation.company_conversion.documented_coverage', return_value=None):
                    reconcile_deposit(deposit)
                deposit.reload(); imported.reload()
                assert money(imported.rows[-1].historical_balance_usd) == money(.01)
            if repair_existing:
                from credinomina_reconciliation.patches.v1_0 import repair_company_difference_client_haircuts as migration
                if closed:
                    frappe.db.set_value(period.doctype, period.name, "status", "Cerrado", update_modified=False)
                scan = migration._pages
                def candidates(getter, doctype, **kwargs):
                    kwargs['filters']['parent'] = deposit.name
                    return scan(getter, doctype, **kwargs)
                with patch.object(migration, '_pages', side_effect=candidates), \
                     patch('credinomina_reconciliation.deposit_reconciliation.reconcile_deposit', side_effect=AssertionError('Patch replayed cash')):
                    repaired = migration.execute()
                    assert repaired['repaired'] == [deposit.name], repaired
                    assert not repaired['review'], repaired
                    versions = frappe.db.count('Version')
                    comments = frappe.db.count('Comment')
                    assert migration.execute() == dict(repaired=[], review={})
                    assert frappe.db.count('Version') == versions and frappe.db.count('Comment') == comments
                deposit.reload(); imported.reload(); period.reload(); other.reload()
                assert money(imported.rows[-1].historical_remitted_usd) == money(26.62)
                assert money(imported.rows[-1].historical_balance_usd) == 0
                assert money(deposit.allocated_usd) == money(490.52)
                assert money(period.deposit_coverage_usd) == money(490.52)
                assert money(period.conversion_coverage_usd) == money(.01)
                assert money(period.pending_usd) == 0
                assert frappe.as_json(other.as_dict()) == other_before
                assert [row.name for row in imported.rows] == source_ids
                if closed:
                    assert period.status == 'Cerrado'
                    return dict(repaired_existing=True, closed_status_preserved=True,
                                no_reconciliation_replayed=True, company_pending=.01, cash_preserved=490.52)
            result = reconcile_deposit(deposit)
            deposit.reload(); imported.reload()
            assert result["detail_pending"] == 0, result
            assert deposit.result == "Conciliado con diferencia documentada", deposit.as_dict()
            assert money(deposit.allocated_usd) == money(490.52)
            assert money(deposit.unallocated_usd) == 0
            assert sum_money(row.historical_balance_usd for row in imported.rows) == 0
            ordinary = [row for row in deposit.detail_rows if row.row_kind != KIND]
            assert [row.deducted_usd for row in ordinary] == amounts
            assert sum_money(row.linked_usd for row in ordinary) == money(490.53)
            assert sum_money(row.pending_usd for row in ordinary) == 0
            diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
            assert diff.difference_notes == "Inmaterial" and not diff.difference_core_item
            assert money(diff.difference_core_pending_usd) == money(.01)
            assert money(diff.pending_usd) == money(-.01)
            before = deposit.allocation_detail
            reconcile_deposit(deposit)
            deposit.reload(); imported.reload()
            other.reload()
            assert frappe.as_json(other.as_dict()) == other_before
            assert [row.name for row in imported.rows] == source_ids
            assert deposit.allocation_detail == before
            assert sum_money(row.historical_balance_usd for row in imported.rows) == 0
            period.reload()
            assert money(imported.rows[-1].historical_remitted_usd) == money(26.62)
            assert money(period.deposit_coverage_usd) == money(490.52), period.as_dict()
            assert money(period.conversion_coverage_usd) == money(.01), period.as_dict()
            assert money(period.complementary_coverage_usd) == 0
            assert money(period.pending_usd) == 0
            from credinomina_reconciliation.accounting_client_summary import build_summary, balance_totals
            summary = balance_totals(imported, build_summary(imported, {}))
            assert money(summary['deposits_usd']) == money(490.52), summary
            assert money(summary['conversion_usd']) == money(.01)
            assert money(summary['balance_usd']) == 0
            from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import execute
            reported = execute(dict(period=period.name))[1][0]
            assert money(reported['deposit_usd']) == money(490.52), reported
            assert money(reported['conversion_usd']) == money(.01)
            assert money(reported['open_usd']) == 0
            detail_report = execute(dict(period=period.name, view='Detalle'))[1]
            assert len([row for row in detail_report if row.get('_company_conversion')]) == 1
            assert all(not row['open_usd'] for row in detail_report)
            from credinomina_reconciliation.deposit_distribution import get_distribution
            distribution = get_distribution(deposit.name)
            assert distribution['consistent'], distribution
            assert distribution['distributed_usd'] == 490.52
            _reconcile_sources(employer.name)
            imported.reload(); deposit.reload(); period.reload()
            assert deposit.allocation_detail == before
            assert money(imported.rows[-1].historical_balance_usd) == 0
            assert money(period.deposit_coverage_usd) == money(490.52)
            reconcile_deposit(other)
            deposit.reload(); imported.reload()
            assert deposit.allocation_detail == before
            assert money(imported.rows[-1].historical_remitted_usd) == money(26.62)
            assert not frappe.db.exists("CN Complementary Item", {"employer": employer.name})
            core = frappe.get_doc(dict(doctype="CN Complementary Item", name=marker + "-CORE", docstatus=1,
                category="Otros ingresos", employer=employer.name, posting_date="2026-03-13",
                amount_usd=-.01, amount=-.01, currency="USD", accounting_source_key=marker,
                period=period.name, reference=marker))
            core.db_insert()
            record(deposit.name, str(deposit.modified), REASON, "Respaldo del core", core.name)
            deposit.reload()
            reconcile_deposit(deposit)
            deposit.reload(); imported.reload(); period.reload()
            assert deposit.result == "Conciliado"
            assert money(deposit.conversion_coverage_usd) == 0
            assert money(period.conversion_coverage_usd) == 0
            assert money(imported.rows[-1].historical_remitted_usd) == money(26.62)
            assert money(imported.rows[-1].historical_balance_usd) == 0
            assert money(deposit.allocated_usd) == money(490.52)
            diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
            assert money(diff.difference_core_pending_usd) == 0
            reconcile_deposit(deposit, undo=True)
            deposit.reload()
            assert money(deposit.allocated_usd) == 0
            assert money(deposit.conversion_coverage_usd) == 0
            return dict(real_cash_applied=490.52, application_pending=0,
                        core_support_pending=.01, original_17_detail_amounts_preserved=True,
                        no_complement_created=True, repeated_distribution_preserved=True,
                        later_core_replaces_company_entry=True, entire_deposit_undo=True)
    finally:
        frappe.db.rollback()
