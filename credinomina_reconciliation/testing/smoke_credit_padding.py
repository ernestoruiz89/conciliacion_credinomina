"""Unpadded core/detail loans match existing padded portfolios; rollback only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.credit_portfolio import enrich_accounting_records
from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "ZEROS-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
            client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                "client_number": marker, "client_name": marker}).insert()
            cut = frappe.get_doc({"doctype": "CN Credit Portfolio Snapshot", "source_file": f"/private/files/{marker}.xlsx",
                "status": "Importado", "report_date": "2091-07-31", "rows": [{"credit_number": "000001807-1",
                    "credit_status": "CANCELADO", "employer": employer.name, "matched_client": client.name,
                    "client_number_core": marker, "client_name": marker, "is_convenio": "Sí",
                    "validation_status": "Cliente y empresa validados", "employer_match_status": "Empresa identificada"}]}).insert()
            record = {"loan_number": "1807-1", "event_type": "Aplicacion", "event_date": "2025-07-15",
                      "currency": "USD", "amount": 50, "amount_usd": 50, "source_key": marker}
            enrich_accounting_records([record], cut.name, employer.name, register_clients=False)
            assert record["portfolio_validation_status"] == "Crédito cancelado; revisar antes de conciliar"
            assert record["loan_number"] == "1807-1"
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-07-01", "reconciliation_mode": "Historica"}).insert()
            record.update(historical_period=period.name, client=client.name)
            imported = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
                "source_file": f"/private/files/{marker}.csv", "status": "Importado", "currency": "USD",
                "historical_period": period.name, "portfolio_snapshot": cut.name, "rows": [record]}).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            clients = [client.as_dict()]
            assert load_detail_loan_clients([{"loan_number": "1807"}], clients, {employer.name}) == {"1807-1": {client.name}}
            deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                "deposit_date": "2025-07-31", "deposit_reference": marker, "deposit_currency": "USD",
                "detail_file": f"/private/files/{marker}-detail.csv",
                "deposit_amount": 50, "detail_periods": [{"period": period.name}]}).insert()
            _apply_remittance_detail(deposit, [{"source_row": 2, "loan_number": "1807-1",
                "client_number": marker, "deducted_usd": 50}], b"test file", f"/private/files/{marker}-detail.csv")
            deposit.reload()
            assert deposit.detail_rows[0].loan_number == "1807-1"
            assert deposit.detail_rows[0].portfolio_credit_status == "CANCELADO"
            assert deposit.detail_rows[0].portfolio_snapshot_used == cut.name
            deposit.submit()
            reconcile_deposit(deposit)
            deposit.reload(); imported.reload(); cut.reload()
            assert deposit.allocated_usd == 50 and deposit.detail_rows[0].pending_usd == 0, (
                deposit.detail_rows[0].match_reason, imported.rows[0].match_reason)
            assert imported.rows[0].loan_number == "1807-1"
            assert cut.rows[0].credit_number == "000001807-1"
            # Existing detail with extra zeros also resolves the same persisted source row.
            assert load_detail_loan_clients([{"loan_number": "00000001807-1"}], clients, {employer.name}) == {"1807-1": {client.name}}
            assert load_detail_loan_clients([{"loan_number": "1807-2"}], clients, {employer.name}) == {}
            return {"core_enriched": True, "detail_without_padding": True, "portfolio_state_found": True,
                    "deposit_reconciled": True, "original_numbers_preserved": True, "cycles_distinct": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
