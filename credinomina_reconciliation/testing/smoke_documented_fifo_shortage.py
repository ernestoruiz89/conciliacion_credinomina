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


def run():
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
            for index, amount in enumerate(amounts):
                identity = f"{marker}-{index}"
                client = frappe.get_doc(dict(doctype="CN Client", employer=employer.name,
                    client_number=identity, client_name=identity)).insert()
                records.append(dict(source_key=identity, event_type="Aplicacion", event_date="2026-03-12",
                    currency="USD", amount=amount, amount_usd=amount, effective=1, client=client.name,
                    client_number=identity, client_name=identity, loan_number=identity + "-1",
                    historical_period=period.name, processing_route="Historica"))
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
            result = reconcile_deposit(deposit)
            deposit.reload(); imported.reload()
            assert result["detail_pending"] == 1, result
            assert deposit.result == "Parcial", deposit.as_dict()
            assert money(deposit.allocated_usd) == money(490.52)
            assert money(deposit.unallocated_usd) == 0
            assert sum_money(row.historical_balance_usd for row in imported.rows) == money(.01)
            ordinary = [row for row in deposit.detail_rows if row.row_kind != KIND]
            assert [row.deducted_usd for row in ordinary] == amounts
            assert sum_money(row.linked_usd for row in ordinary) == money(490.52)
            assert sum_money(row.pending_usd for row in ordinary) == money(.01)
            diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
            assert diff.difference_notes == "Inmaterial" and not diff.difference_core_item
            assert money(diff.difference_core_pending_usd) == money(.01)
            assert money(diff.pending_usd) == money(-.01)
            before = deposit.allocation_detail
            reconcile_deposit(deposit)
            deposit.reload(); imported.reload()
            assert deposit.allocation_detail == before
            assert sum_money(row.historical_balance_usd for row in imported.rows) == money(.01)
            assert not frappe.db.exists("CN Complementary Item", {"employer": employer.name})
            return dict(real_cash_applied=deposit.allocated_usd, application_pending=.01,
                        core_support_pending=.01, original_17_detail_amounts_preserved=True,
                        no_complement_created=True, repeated_distribution_preserved=True)
    finally:
        frappe.db.rollback()
