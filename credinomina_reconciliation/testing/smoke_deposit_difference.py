"""Rollback-only integration of company differences and their later core evidence."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_difference import record, KIND, SETTLED
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.rounding import money


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            reason = frappe.get_doc(dict(doctype="CN Difference Reason", reason_name="TEST-" + frappe.generate_hash(length=8))).insert()
            for delta in (-.03, .03):
                label = "DIFF-" + frappe.generate_hash(length=8)
                company = frappe.get_doc(dict(doctype="CN Employer", employer_name=label, employer_code=label)).insert()
                client = frappe.get_doc(dict(doctype="CN Client", client_number=label, client_name=label, employer=company.name)).insert()
                period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                    payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
                imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, status="Importado",
                    source_file=f"/private/files/{label}.csv", historical_backfill=1, historical_period=period.name,
                    rows=[dict(source_row=2, source_key=label, event_type="Aplicacion", event_date="2025-04-30",
                        client_name=label, client_number=client.name, client=client.name, loan_number=label, reference=label,
                        currency="USD", amount=100, amount_usd=100, processing_route="Historica", historical_period=period.name,
                        effective=1, match_status="Pendiente")])).insert()
                from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
                _reconcile_sources(company.name, preserve_deposits=True)
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_reference=label, deposit_date="2025-05-10", deposit_currency="USD", deposit_amount=100 + delta,
                    detail_file=f"/private/files/{label}.xlsx", detail_source_file=f"/private/files/{label}.xlsx",
                    detail_hash="TEST", apply_fifo=1, detail_periods=[dict(period=period.name)],
                    detail_rows=[dict(client_name=label, client_number=client.name, client=client.name,
                        employer=company.name, loan_number=label, deducted_usd=100, amount_usd=100)])).insert()
                deposit.submit()
                deposit.reload()
                record(deposit.name, str(deposit.modified), reason.name, "Diferencia revisada por la empresa")
                deposit.reload()
                diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
                diff_name = diff.name
                assert money(diff.difference_amount_usd) == money(delta)
                reconcile_deposit(deposit)
                deposit.reload(); imported.reload()
                assert deposit.result == SETTLED, (deposit.result, deposit.detail_status, [r.as_dict() for r in deposit.detail_rows])
                assert money(deposit.allocated_usd) == min(money(100), money(100 + delta))
                assert money(deposit.unallocated_usd) == max(money(delta), money(0))
                assert money(imported.rows[0].historical_balance_usd) == max(-money(delta), money(0))
                diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
                assert money(diff.difference_core_pending_usd) == abs(money(delta))
                _reconcile_sources(company.name, preserve_deposits=False)
                deposit.reload()
                assert deposit.result == SETTLED, deposit.as_dict()
                # Later the core supplies the signed counterpart. This fixture
                # inserts imported evidence directly; no manual creation API.
                core = frappe.get_doc(dict(doctype="CN Complementary Item", name=label, docstatus=1,
                    category="Otros ingresos", employer=company.name, posting_date="2025-05-10",
                    amount_usd=delta, amount=delta, currency="USD", accounting_source_key=label,
                    period=period.name, reference=label))
                core.db_insert()
                record(deposit.name, str(deposit.modified), reason.name, "Partida importada verificada", core.name)
                deposit.reload()
                reconcile_deposit(deposit)
                deposit.reload(); imported.reload()
                assert deposit.result == "Conciliado", deposit.as_dict()
                diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
                assert diff.name == diff_name and diff.difference_core_status == "Conciliada contra core"
                assert money(diff.difference_core_pending_usd) == 0
                assert money(imported.rows[0].historical_balance_usd) == 0
                assert money(deposit.allocated_usd) == money(deposit.amount_usd)
                before = deposit.allocation_detail
                reconcile_deposit(deposit)
                deposit.reload()
                assert deposit.allocation_detail == before
                _reconcile_sources(company.name, preserve_deposits=False)
                deposit.reload()
                assert deposit.result == "Conciliado"
                assert deposit.allocation_detail == before
                assert money(deposit.detail_rows[0].deducted_usd) == 100
                assert money(deposit.deposit_amount) == money(100 + delta)
                assert not frappe.db.exists("CN Complementary Item", {"employer": company.name, "category": "Diferencia por tolerancia"})
                reconcile_deposit(deposit, undo=True)
                deposit.reload()
                diff = next(row for row in deposit.detail_rows if row.row_kind == KIND)
                assert money(diff.difference_core_pending_usd) == abs(money(delta))
                assert diff.difference_core_item == core.name
                record(deposit.name, str(deposit.modified), remove=1)
                deposit.reload()
                assert all(row.row_kind != KIND for row in deposit.detail_rows)
                assert money(deposit.detail_rows[0].deducted_usd) == 100
            return {"both_signs": True, "actual_cash_only": True, "later_core_same_row": True,
                    "repeat_without_duplicates": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
