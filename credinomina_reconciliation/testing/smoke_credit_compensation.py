"""Rollback-only integration: core debit vs customer excess, no cash/refund changes."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.client_credit import FINANCIAL_FIELDS, MANAGED_FIELDS


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "CREDIT-OFFSET-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=employer.name, client_name="DELVIN FRANCISCO GUTIERREZ BOBADILLA",
                                        client_number=marker)).insert()
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_reference=marker, deposit_date="2025-08-07", deposit_currency="USD", deposit_amount=19.25)).insert()
            deposit.submit()
            credit = frappe.get_doc(dict(doctype="CN Complementary Item", category="Saldo a favor del cliente",
                posting_date="2025-08-07", currency="USD", amount=19.25, employer=employer.name,
                registered_deposit=deposit.name, credit_client=client.name, credit_assigned_to="Administrator",
                credit_commitment_date="2026-10-10", credit_treatment="Pendiente de decisión",
                reason_type="Error de la empresa", description="Saldo a favor del cliente")).insert()
            credit.submit()
            deposit.reload()
            stored_deposit = json.dumps(deposit.as_dict(), sort_keys=True, default=str)
            protected = {field: credit.get(field) for field in (*FINANCIAL_FIELDS, *MANAGED_FIELDS, "result")}
            ledger = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar", review_action="Pendiente de revisión",
                posting_date="2025-11-21", currency="USD", amount=19.25, employer=employer.name,
                description="Traslado del saldo a favor a la cuenta 3001", voucher=marker, voucher_line=marker,
                accounting_source_key=marker, source_account="160209013004", source_currency="NIO",
                source_debit=704.89, source_credit=0, source_fx_rate=36.6243, source_voucher=marker,
                source_date="2025-11-21", source_file="/private/files/credit-offset-test.xlsx",
                source_file_hash=marker, source_row=20936, source_client_name=client.client_name,
                accounting_classification="Movimiento interno")).insert()
            preview = offsets.preview_compensation(ledger.name, credit.name)
            assert preview["suggested_usd"] == 19.25
            args = (ledger.name, credit.name, 19.25, "2025-11-21", "Traslado a 3001 verificado", preview["request_key"])
            offsets.confirm_compensation(*args)
            offsets.confirm_compensation(*args)
            credit.reload(); ledger.reload(); deposit.reload()
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            assert {field: credit.get(field) for field in protected} == protected
            assert ledger.docstatus == 1 and ledger.category == offsets.CATEGORY
            assert ledger.compensated_usd == credit.compensated_usd == 19.25
            assert ledger.compensation_pending_usd == credit.compensation_pending_usd == 0
            assert len(credit.compensations) == len(ledger.compensations) == 1
            assert financial_balance(ledger.as_dict())["pending_usd"] == 0
            assert financial_balance(credit.as_dict())["management_pending_usd"] == 19.25
            assert ledger.source_debit == 704.89 and ledger.source_credit == 0
            choice = offsets.get_reversible_compensations(credit.name)
            offsets.reverse_compensation(credit.name, preview["request_key"], "2025-11-22", "Corregir vínculo", choice["request_key"])
            credit.reload(); ledger.reload(); deposit.reload()
            assert credit.compensation_pending_usd == ledger.compensation_pending_usd == 19.25
            assert {field: credit.get(field) for field in protected} == protected
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            return {"paired_offset_and_retry": "OK", "preserved_deposit_and_refund_management": "OK",
                    "core_pending_zero": "OK", "paired_reversal": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
