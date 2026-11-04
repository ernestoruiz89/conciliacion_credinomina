"""A positive core counterpart adds to confirmed FIFO without replaying it."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run(reimported=False):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            marker = "FIFO-CORE-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_number=marker, client_name="Cliente de prueba")).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-08-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                historical_backfill=1, historical_period=period.name, status="Importado", currency="USD",
                source_file=f"/private/files/{marker}.csv", rows=[dict(source_key=marker, event_type="Aplicacion",
                    event_date="2025-08-15", currency="USD", amount=18.08, amount_usd=18.08, effective=1,
                    client=client.name, client_number=client.name, client_name=client.client_name, loan_number=marker,
                    processing_route="Historica", historical_period=period.name)])).insert()
            _reconcile_sources(company.name, preserve_deposits=True)
            fixed = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker + "FIXED", deposit_date="2025-09-01", deposit_currency="USD", deposit_amount=5,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=5)])).insert()
            fixed.submit(); reconcile_deposit(fixed)
            fixed_state = document_state(fixed.reload())
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-09-15", deposit_currency="USD", deposit_amount=26.17,
                apply_fifo=1, detail_file=f"/private/files/{marker}.xlsx", detail_source_file=f"/private/files/{marker}.xlsx",
                detail_hash="TEST", detail_periods=[dict(period=period.name)],
                detail_rows=[dict(source_row=2, client=client.name, client_number=client.name, client_name=client.client_name,
                    loan_number=marker, employer=company.name, deducted_usd=26.17, amount_usd=26.17)])).insert()
            deposit.submit(); reconcile_deposit(deposit); deposit.reload()
            assert money(deposit.allocated_usd) == money(13.08)
            original = json.loads(deposit.allocation_detail)
            if reimported:
                # Simulate the former importer: identical file, new child ID,
                # lost resolved credit and cleared matching cache, while the
                # confirmed ledger and source payment still reference OLD.
                removed = deposit.detail_rows[0].as_dict()
                new_name = frappe.generate_hash(length=10)
                frappe.db.sql("UPDATE `tabCN Remittance Detail` SET name=%s WHERE parent=%s AND name=%s",
                    (new_name, deposit.name, removed["name"]))
                frappe.db.set_value("CN Remittance Detail", new_name,
                    dict(loan_number="", matched_targets="[]", linked_usd=0, pending_usd=26.17), update_modified=False)
                deposit.reload()
                frappe.get_doc(dict(doctype="Version", ref_doctype=deposit.doctype, docname=deposit.name,
                    data=json.dumps(dict(changed=[["detail_status", "Parcial", "Cargado"]],
                        removed=[["detail_rows", removed]], added=[["detail_rows", deposit.detail_rows[0].as_dict()]]), default=str))).insert(ignore_permissions=True)
            core = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name, category="Otros ingresos",
                review_action="Partida de depósito", amount_reviewed=1, currency="USD", amount=13.08,
                reference=marker, posting_date="2025-11-21", description="Partida del core para el remanente",
                accounting_source_key=marker, source_date="2025-11-21", source_currency="USD", source_debit=13.08,
                source_credit=0, source_client_name=client.client_name))
            with imported_item(marker):
                core.insert()
            core.submit()
            deposit.append("targets", dict(complementary_item=core.name, amount_usd=13.08, detail_row=deposit.detail_rows[0].name))
            deposit.save()
            if reimported:
                before_failure = document_state(deposit.reload())
                before_row = deposit.detail_rows[0].as_dict()
                versions = frappe.db.count("Version", {"ref_doctype": deposit.doctype, "docname": deposit.name})
                frappe.db.savepoint("reimport_recovery_failure")
                try:
                    with patch("credinomina_reconciliation.deposit_reconciliation.assert_preserved_allocations",
                               side_effect=RuntimeError("Injected failure after recovery")):
                        reconcile_deposit(deposit)
                    raise AssertionError("Expected injected failure")
                except RuntimeError as error:
                    assert "Injected failure" in str(error)
                    frappe.db.rollback(save_point="reimport_recovery_failure")
                assert document_state(deposit.reload()) == before_failure
                assert deposit.detail_rows[0].as_dict() == before_row
                assert frappe.db.count("Version", {"ref_doctype": deposit.doctype, "docname": deposit.name}) == versions
                assert document_state(fixed.reload()) == fixed_state
                original[0]["fila_detalle"] = deposit.detail_rows[0].name
            reconcile_deposit(deposit); deposit.reload(); source.reload()
            ledger = json.loads(deposit.allocation_detail)
            fifo = [entry for entry in ledger if entry.get("aplicacion_id") == source.rows[0].name]
            assert fifo == original
            assert len(ledger) == 2 and money(deposit.allocated_usd) == money(26.16)
            assert money(deposit.unallocated_usd) == money(.01)
            assert money(deposit.detail_rows[0].pending_usd) == money(.01)
            assert deposit.targets[0].result == "Aplicada"
            assert money(source.rows[0].historical_remitted_usd) == money(18.08)
            assert document_state(fixed.reload()) == fixed_state
            if reimported:
                assert deposit.detail_rows[0].loan_number == marker
                assert len(frappe.get_all("Version", filters={"ref_doctype": deposit.doctype, "docname": deposit.name,
                    "data": ["like", '%Recuperar trazabilidad de detalle reimportado%']}, pluck="name")) == 1
            from credinomina_reconciliation.complementary_balances import load_balances
            balance = load_balances([core])[core.name]
            assert money(balance["used_usd"]) == money(13.08) and not money(balance["pending_usd"])
            for _ in range(2):
                reconcile_deposit(deposit); deposit.reload()
                assert json.loads(deposit.allocation_detail) == ledger
                assert money(deposit.allocated_usd) == money(26.16)
                assert money(deposit.detail_rows[0].pending_usd) == money(.01)
                assert document_state(fixed.reload()) == fixed_state
                balance = load_balances([core])[core.name]
                assert money(balance["used_usd"]) == money(13.08) and not money(balance["pending_usd"])
            return dict(confirmed_fifo=13.08, positive_core=13.08, distributed=26.16, remaining=.01,
                reimport_recovered=reimported, repeated_without_duplicates=True, other_deposit_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
