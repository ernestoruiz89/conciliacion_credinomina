"""Rollback-only test of an imported debit linked to customer cash."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reclassification_deposit import link, preview, validate_pair
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "RECLASS-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_name="Cliente reclasificación",
                                         client_number=marker)).insert()
            loan = marker + "-1"
            frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot", source_file=f"/private/files/{marker}.xlsx",
                status="Importado", report_date="2039-04-28", rows=[dict(credit_number=loan, credit_status="CANCELADO",
                    employer=company.name, matched_client=client.name, client_number_core=marker, client_name=client.client_name)])).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica", historical_scope="Fecha exacta",
                historical_application_date="2025-07-31", collection_cycle="Mensual")).insert()
            file = f"/private/files/{marker}.csv"
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-08-07", deposit_currency="USD", deposit_amount=19.25,
                detail_file=file, detail_source_file=file, detail_hash=marker, detail_count=1,
                detail_periods=[dict(period=period.name)], detail_rows=[dict(client=client.name,
                    client_name=client.client_name, client_number=marker, employer=company.name,
                    loan_number=loan, deducted_usd=19.25, source_row=2)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype="CN Complementary Item", category="Por clasificar",
                    review_action="Pendiente de revisión", employer=company.name, posting_date="2025-11-21",
                    client_name=client.client_name, client_number=marker, loan_number=loan, currency="USD", amount=19.25,
                    source_date="2025-11-21", source_currency="USD", source_debit=19.25, source_credit=0,
                    accounting_source_key=marker, source_voucher=marker, voucher=marker, voucher_line=marker,
                    description="Reclasificación de saldo a favor", source_description="Reclasificación de saldo a favor")).insert()
            # The fixture represents an existing submitted compensation item from the core.
            frappe.db.set_value(item.doctype, item.name, dict(docstatus=1, category="Compensación entre partidas",
                review_action="Compensación entre partidas", compensation_status="Sin compensar",
                compensation_pending_usd=19.25), update_modified=False)
            item.reload(); deposit.reload()
            assert deposit.detail_rows[0].client, deposit.detail_rows[0].as_dict()
            validate_pair(item, deposit, deposit.detail_rows[0])
            context = preview(item.name, deposit.name)
            assert len(context["rows"]) == 1, context
            with patch("credinomina_reconciliation.deposit_reconciliation.reconcile_deposit",
                       side_effect=frappe.ValidationError("Conciliación rechazada")):
                try:
                    link(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                         str(deposit.modified), "2026-11-30", "Prueba de transacción")
                    raise AssertionError("Se aceptó la conciliación fallida")
                except frappe.ValidationError:
                    pass
            item.reload(); deposit.reload()
            assert item.category == "Compensación entre partidas" and not item.registered_deposit
            assert deposit.detail_rows[0].pending_usd == 19.25
            result = link(item.name, deposit.name, deposit.detail_rows[0].name, str(item.modified),
                          str(deposit.modified), "2026-11-30", "Reclasificación identificada por crédito y asiento")
            item.reload(); deposit.reload()
            row = deposit.detail_rows[0]
            assert result["credit_pending_usd"] == 19.25
            assert str(item.credit_origin_date) == "2025-08-07" and str(item.reclassification_date) == "2025-11-21"
            assert (row.pending_usd, row.client_credit_usd, row.match_status) == (0, 19.25, "Conciliada"), row.as_dict()
            assert item.result == "Saldo a favor documentado" and item.credit_pending_usd == 19.25
            assert item.source_debit == 19.25 and item.source_credit == 0
            from credinomina_reconciliation.complementary_state import refresh
            refresh([item.as_dict()])
            item.reload()
            assert (item.reconciliation_status, item.applied_usd, item.pending_usd) == ("Conciliado", 19.25, 0)
            item.save()  # The ordinary submitted-document guards retain both dates and identities.
            item.reload()
            assert not any(entry.get("partida") == item.name for entry in json.loads(deposit.allocation_detail or "[]"))
            assert len(frappe.get_all("CN Complementary Item", filters={"accounting_source_key": marker})) == 1
            from credinomina_reconciliation.client_credit import record_management
            management = record_management(item.name, str(item.modified), "Devolución", 5, "2025-08-10", marker)
            assert management["pending_usd"] == 14.25  # A real later action, not the reconciliation, consumes credit.
            deposit.reload()
            assert deposit.detail_rows[0].pending_usd == 0 and deposit.justified_surplus_usd == 19.25
            return dict(linked=True, detail_reconciled=True, client_credit_pending=19.25,
                        no_duplicate_item=True, rolled_back=True)
    finally:
        frappe.db.rollback()
