"""Duplicate client payment documented for the paying company; rollback only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import create_complementary_item


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "COMPANY-ROW-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
            client = frappe.get_doc({"doctype": "CN Client", "employer": employer.name,
                "client_name": "Cliente pago duplicado", "client_number": marker}).insert()
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-07-01", "reconciliation_mode": "Historica", "collection_cycle": "Mensual",
                "historical_scope": "Fecha exacta", "historical_application_date": "2025-07-15"}).insert()
            source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name, "currency": "USD",
                "source_file": f"/private/files/{marker}.csv", "status": "Importado", "rows": [{
                    "event_type": "Aplicacion", "event_date": "2025-07-15", "source_key": marker,
                    "client_name": client.client_name, "client_number": marker, "client": client.name,
                    "loan_number": "001102-1", "amount": 309.12, "amount_usd": 309.12, "currency": "USD",
                    "effective": 1, "historical_period": period.name, "processing_route": "Historica"}]}).insert()
            _reconcile_sources(employer.name)
            source.reload()
            deposits = []
            for index, day in enumerate(("15", "30")):
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
                    "deposit_date": f"2025-07-{day}", "deposit_reference": marker + day, "deposit_currency": "USD",
                    "deposit_amount": 309.12, "detail_periods": [{"period": period.name}],
                    "detail_file": f"/private/files/{marker}{day}.csv", "detail_source_file": f"/private/files/{marker}{day}.csv",
                    "detail_hash": marker + day, "detail_rows": [{"client": client.name, "client_number": marker,
                        "client_name": client.client_name, "loan_number": "001102-1", "deducted_usd": 309.12, "source_row": 2}]}).insert()
                if index == 0:
                    deposit.append("targets", {"historical_application": source.rows[0].name, "amount_usd": 309.12,
                                              "detail_row": deposit.detail_rows[0].name})
                deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload()
                deposits.append(deposit)
            original, duplicate = deposits
            assert original.allocated_usd == 309.12, (original.result, [r.as_dict() for r in original.detail_rows])
            assert duplicate.allocated_usd == 0 and duplicate.detail_rows[0].pending_usd == 309.12
            original_before = document_state(original)
            row_id = duplicate.detail_rows[0].name
            values = {"category": "Saldo a favor de la empresa", "currency": "USD", "posting_date": "2025-07-30",
                "credit_detail_row": row_id, "reason_type": "Error de la empresa", "description": f"Pago duplicado; original {original.name}",
                "credit_treatment": "Devolución", "credit_assigned_to": "Administrator", "credit_commitment_date": "2026-10-10"}
            for amount, expected_pending in ((100, 209.12), (209.12, 0)):
                result = create_complementary_item(duplicate.name, str(duplicate.modified), dict(values, amount=amount))
                item = frappe.get_doc("CN Complementary Item", result["name"])
                assert item.result == "Saldo a favor documentado" and item.credit_detail_row == row_id
                assert not item.credit_client and not item.client_number and not item.loan_number
                assert item.employer == employer.name
                duplicate.reload()
                row = duplicate.detail_rows[0]
                assert row.pending_usd == expected_pending, row.as_dict()
                assert row.client_credit_usd == row.linked_usd == 0
                assert row.client == client.name and row.loan_number == "001102-1"
            assert row.company_credit_usd == 309.12 and row.match_status == "Conciliada"
            assert duplicate.allocated_usd == 0 and duplicate.justified_surplus_usd == 309.12 and duplicate.unclassified_usd == 0
            assert "Saldo a favor de la empresa" in row.matched_targets_summary
            for _ in range(2):
                reconcile_deposit(duplicate)
                duplicate.reload()
                assert duplicate.detail_rows[0].company_credit_usd == 309.12
                assert duplicate.detail_rows[0].pending_usd == 0
            original.reload()
            assert document_state(original) == original_before
            frappe.db.savepoint("reject_duplicate_credit")
            try:
                create_complementary_item(duplicate.name, str(duplicate.modified), dict(values, amount=0.01))
            except frappe.ValidationError:
                frappe.db.rollback(save_point="reject_duplicate_credit")
            else:
                raise AssertionError("Duplicate reservation accepted")
            # Cancellation releases only this company's documented amount.
            item.cancel()
            duplicate.reload()
            assert duplicate.detail_rows[0].company_credit_usd == 100
            assert duplicate.detail_rows[0].pending_usd == 209.12
            return {"duplicate_amount": 309.12, "first_deposit_preserved": True, "company_beneficiary": True,
                    "row_resolved_without_loan_payment": True, "partial_and_repeat_safe": True,
                    "cancellation_reopens_pending": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
