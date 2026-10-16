"""Rollback-only integration: core debit vs customer excess, no cash/refund changes."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.client_credit import FINANCIAL_FIELDS


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
                deposit_reference=marker, deposit_date="2025-05-15", deposit_currency="USD", deposit_amount=32.63)).insert()
            deposit.submit()
            credit = frappe.get_doc(dict(doctype="CN Complementary Item", category="Saldo a favor del cliente",
                posting_date="2025-05-15", currency="USD", amount=32.63, employer=employer.name,
                registered_deposit=deposit.name, credit_client=client.name, credit_assigned_to="Administrator",
                credit_commitment_date="2026-10-10", credit_treatment="Pendiente de decisión",
                reason_type="Error de la empresa", description="Saldo a favor del cliente")).insert()
            credit.submit()
            from credinomina_reconciliation import credit_application
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2025-05-01', reconciliation_mode='Historica',
                historical_scope='Fecha exacta', historical_application_date='2025-05-15')).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file='/private/files/' + marker + '.csv', currency='USD', status='Importado', rows=[dict(
                    event_type='Aplicacion', event_date='2025-05-15', source_key=marker,
                    client=client.name, client_name=client.client_name, client_number=marker,
                    amount=14.17, amount_usd=14.17, currency='USD', effective=1,
                    historical_period=period.name, processing_route='Historica', voucher=marker)])).insert()
            _reconcile_sources(employer.name)
            credit.reload(); imported.reload()
            credit_application.apply(credit.name, str(credit.modified), imported.rows[0].name, 14.17, '2025-05-15')
            credit.reload(); period.reload()
            assert period.pending_usd == 0 and credit.credit_pending_usd == 18.46
            deposit.reload()
            stored_deposit = json.dumps(deposit.as_dict(), sort_keys=True, default=str)
            protected = {field: credit.get(field) for field in (*FINANCIAL_FIELDS, "credit_history", "result")}
            ledger = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar", review_action="Pendiente de revisión",
                posting_date="2025-06-01", currency="USD", amount=18.46, employer=employer.name,
                description="Traslado del saldo a favor a la cuenta 3001", voucher=marker, voucher_line=marker,
                accounting_source_key=marker, source_account="160209013004", source_currency="NIO",
                source_debit=676.08, source_credit=0, source_fx_rate=36.6243, source_voucher=marker,
                source_date="2025-06-01", source_file="/private/files/credit-offset-test.xlsx",
                source_file_hash=marker, source_row=20936, source_client_name=client.client_name,
                accounting_classification="Movimiento interno")).insert()
            preview = offsets.preview_compensation(ledger.name, credit.name)
            assert preview["suggested_usd"] == 18.46
            args = (ledger.name, credit.name, 18.46, "2026-10-07", "Traslado a 3001 verificado", preview["request_key"])
            offsets.confirm_compensation(*args)
            offsets.confirm_compensation(*args)
            credit.reload(); ledger.reload(); deposit.reload()
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            assert {field: credit.get(field) for field in protected} == protected
            assert ledger.docstatus == 1 and ledger.category == offsets.CATEGORY
            assert ledger.compensated_usd == credit.compensated_usd == 18.46
            assert ledger.compensation_pending_usd == 0 and credit.compensation_pending_usd == 14.17
            assert len(credit.compensations) == len(ledger.compensations) == 1
            assert financial_balance(ledger.as_dict())["pending_usd"] == 0
            assert financial_balance(credit.as_dict())["management_pending_usd"] == 0
            assert credit.credit_resolved_usd == 32.63 and credit.credit_management_status == 'Resuelto'
            assert ledger.source_debit == 676.08 and ledger.source_credit == 0
            choice = offsets.get_reversible_compensations(credit.name)
            offsets.reverse_compensation(credit.name, preview["request_key"], "2026-10-15", "Corregir vínculo", choice["request_key"])
            credit.reload(); ledger.reload(); deposit.reload()
            assert credit.compensation_pending_usd == 32.63 and ledger.compensation_pending_usd == 18.46
            assert credit.credit_pending_usd == 18.46 and credit.credit_resolved_usd == 14.17
            assert {field: credit.get(field) for field in protected} == protected
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            correction = offsets.get_date_correction_candidates(credit.name)
            correction_args = (credit.name, preview["request_key"], "2025-06-01", "Fecha efectiva del asiento", correction["request_key"])
            offsets.correct_compensation_date(*correction_args)
            offsets.correct_compensation_date(*correction_args)
            credit.reload(); ledger.reload(); deposit.reload()
            assert len(credit.compensations) == len(ledger.compensations) == 5
            for doc in (credit, ledger):
                assert offsets.balance(doc, '2025-05-31')['compensated_usd'] == 0
                for cutoff in ('2025-06-01', '2026-08-31', '2026-10-07', '2026-10-14', '2026-10-15'):
                    assert offsets.balance(doc, cutoff)['compensated_usd'] == 18.46
            assert {field: credit.get(field) for field in protected} == protected
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute
            report_rows = execute({'employer': employer.name, 'cutoff_date': '2026-08-31', 'view_mode': 'Detalle'})[1]
            assert not any(row['source_document'] == ledger.name for row in report_rows), report_rows
            assert not any(row['source_document'] == credit.name for row in report_rows), report_rows
            assert credit.credit_pending_usd == 0 and credit.credit_resolved_usd == 32.63
            # Existing caches are repaired from the two journals without touching
            # deposit allocations or correction history, and a rerun is harmless.
            from credinomina_reconciliation.patches.v1_0.include_compensations_in_credit_usage import execute as migrate
            frappe.db.set_value(credit.doctype, credit.name, dict(credit_pending_usd=18.46, credit_resolved_usd=14.17))
            migrate()
            migrate()
            credit.reload(); deposit.reload()
            assert credit.credit_pending_usd == 0 and credit.credit_resolved_usd == 32.63
            assert json.dumps(deposit.as_dict(), sort_keys=True, default=str) == stored_deposit
            return {"paired_offset_and_retry": "OK", "preserved_deposit_and_management_history": "OK",
                    "available_credit_and_migration": "OK",
                    "core_pending_zero": "OK", "paired_reversal": "OK", "date_correction_and_cutoff": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
