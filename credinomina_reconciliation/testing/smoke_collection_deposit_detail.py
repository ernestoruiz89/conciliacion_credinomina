"""Generate editable detail from multiple payrolls; rollback all database fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.collection_deposit_detail import (
    collection_detail_available, preview_collection_detail, use_collection_detail,
)
from credinomina_reconciliation.parsers import parse_collection_file


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "COL-DETAIL-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Mensual")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", client_number=marker,
                client_name="Ana Prueba " + marker, employer=employer.name)).insert()
            # Credit identity is verified against portfolio evidence, as with
            # uploaded detail. No accounting applications are needed here.
            snapshot = frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot", name=marker,
                report_date="2026-09-30", status="Importado", disabled=0))
            snapshot.db_insert()
            frappe.get_doc(dict(doctype="CN Credit Portfolio Row", name=marker + "-LOAN",
                parent=snapshot.name, parenttype=snapshot.doctype, parentfield="rows", idx=1,
                credit_number="123-1", employer=employer.name, matched_client=client.name,
                client_number_core=client.client_number, client_name=client.client_name)).db_insert()
            periods = []
            for month, amount in (("2026-09-01", 100), ("2026-10-01", 50)):
                period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                    payroll_month=month, reconciliation_mode="Operativa", collection_cycle="Mensual",
                    application_basis="Cobranza", collection_rows=[dict(client=client.name,
                        client_name=client.client_name, loan_number="123-1", row_key=marker + month,
                        expected_usd=amount)])).insert()
                period.reload()
                periods.append(period)
            names = [period.name for period in periods]
            assert collection_detail_available(employer.name, names)
            snapshots = [period.as_dict() for period in periods]
            for state in (0, 1):
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                    deposit_date="2026-10-09", deposit_currency="USD", deposit_amount=125,
                    deposit_reference=marker + str(state), detail_periods=[dict(period=name) for name in names])).insert()
                if state:
                    deposit.submit()
                deposit.reload()
                preview = preview_collection_detail(deposit.name)
                assert preview["total_usd"] == 150 and len(preview["rows"]) == 2
                key = preview["rows"][0]["claim_id"]
                with patch("credinomina_reconciliation.deposit_reconciliation.reconcile_deposit",
                           side_effect=AssertionError("Must not reconcile")):
                    result = use_collection_detail(deposit.name, preview["fingerprint"],
                        selected_claim_ids=[key], selected_amounts={key: "125.00"})
                deposit.reload()
                assert deposit.docstatus == state and deposit.result == "Pendiente"
                assert deposit.detail_origin == "Cobranza de los períodos"
                assert deposit.detail_total_usd == 125 and len(deposit.detail_rows) == 1
                assert deposit.detail_rows[0].client == client.name
                assert deposit.detail_rows[0].row_key == periods[0].collection_rows[0].row_key
                assert "cobranza original US$ 100.00; detalle US$ 125.00" in deposit.detail_rows[0].comments
                assert deposit.detail_rows[0].pending_usd == 125 and not deposit.allocation_detail
                file = frappe.get_doc("File", {"file_url": result["file_url"]})
                assert file.is_private
                parsed = parse_collection_file(file.file_name, file.get_content(), require_deduction=True, require_name=True)
                assert parsed[0]["deducted_usd"] == 125
                replacement = preview_collection_detail(deposit.name)
                assert replacement["replaces_detail"]
                use_collection_detail(deposit.name, replacement["fingerprint"], True,
                    [key], {key: "75.00"})
                deposit.reload()
                assert deposit.detail_total_usd == 75
                assert frappe.db.exists("File", file.name)
            for period, snapshot in zip(periods, snapshots):
                period.reload()
                assert period.as_dict() == snapshot
            return dict(multiple_periods=True, draft_and_submitted=True, edited_amounts=True,
                private_workbook=True, explicit_replacement=True, collection_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
