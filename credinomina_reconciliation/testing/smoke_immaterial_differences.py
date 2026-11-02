"""Real reconciliation and backfill, rolled back on the dedicated test site."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.difference_reasons import seed_reasons
from credinomina_reconciliation.immaterial_differences import REASON, NOTES
from credinomina_reconciliation.patches.v1_0 import classify_immaterial_differences as migration
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail


def financial_state(document):
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()
                    if not key.startswith("_") and key not in {"difference_reason", "difference_notes", "modified", "modified_by"}}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return frappe.as_json(clean(document.as_dict()))


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"):
            seed_reasons()
            documents = []
            for amount in (9.99, 10.01):
                marker = "CENT-" + frappe.generate_hash(length=8)
                company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                    employer_code=marker, payroll_frequency="Mensual")).insert()
                client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                    client_number=marker, client_name=marker)).insert()
                period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                    payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
                source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                    source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD",
                    rows=[dict(source_key=marker, event_type="Aplicacion", event_date="2025-04-15",
                        currency="USD", amount=10, amount_usd=10, effective=1, client=client.name,
                        client_number=marker, client_name=marker, loan_number=marker + "-1",
                        historical_period=period.name, processing_route="Historica")])).insert()
                _reconcile_sources(company.name)
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_reference=marker, deposit_date="2025-04-30", deposit_currency="USD",
                    deposit_amount=amount, apply_fifo=1, detail_periods=[dict(period=period.name)],
                    detail_file=f"/private/files/{marker}.xlsx")).insert()
                _apply_remittance_detail(deposit, [dict(source_row=2, client_number=marker,
                    client_name=marker, loan_number=marker + "-1", deducted_usd=amount)],
                    marker.encode(), deposit.detail_file)
                deposit.reload()
                deposit.submit()
                reconcile_deposit(deposit)
                deposit.reload(); source.reload(); period.reload()
                if amount == 9.99:
                    assert source.rows[0].historical_balance_usd == .01, (source.rows[0].historical_balance_usd,
                        deposit.allocated_usd, deposit.result, deposit.detail_rows[0].match_reason)
                    assert source.rows[0].difference_reason == REASON
                    assert source.rows[0].difference_notes == NOTES
                else:
                    assert deposit.unclassified_usd == .01
                    assert deposit.detail_rows[0].pending_usd == .01
                    assert deposit.difference_reason == deposit.detail_rows[0].difference_reason == REASON
                    assert deposit.difference_notes == deposit.detail_rows[0].difference_notes == NOTES
                before = [financial_state(doc) for doc in (source, deposit, period)]
                reconcile_deposit(deposit)
                for doc in (source, deposit, period):
                    doc.reload()
                assert before == [financial_state(doc) for doc in (source, deposit, period)]
                documents.extend((source, deposit, period))

            # First-stage collection/application conversion differences are
            # documented independently of an outstanding deposit.
            loan = marker + "-OPER-1"
            operative = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2026-09-01", reconciliation_mode="Operativa", collection_cycle="Mensual",
                collection_rows=[dict(row_key=marker + "OPER", client=client.name, client_name=marker,
                    loan_number=loan, expected_usd=10.01)])).insert()
            operative_source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                source_file=f"/private/files/{marker}-OPER.csv", status="Importado", currency="USD",
                rows=[dict(source_key=marker + "OPER", event_type="Aplicacion", event_date="2026-09-30",
                    currency="USD", amount=10, amount_usd=10, effective=1, client=client.name,
                    client_number=marker, client_name=marker, loan_number=loan, processing_route="Operativa",
                    historical_period=operative.name)])).insert()
            _reconcile_sources(company.name)
            operative.reload(); operative_source.reload()
            assert operative.collection_rows[0].quality_difference_usd == -.01, (
                operative.collection_rows[0].applied_usd, operative_source.rows[0].match_reason,
                operative_source.rows[0].processing_route, operative_source.rows[0].historical_period)
            assert operative.collection_rows[0].difference_reason == REASON
            documents.extend((operative, operative_source))

            # Existing classifications must survive the migration. Other rows
            # lack the labels, including evidence belonging to a closed period.
            for doc in documents:
                rows = ([doc] + list(doc.get("detail_rows") or []) + list(doc.get("rows") or [])
                        + list(doc.get("collection_rows") or []))
                for row in rows:
                    if row.get("difference_reason") == REASON:
                        frappe.db.set_value(row.doctype, row.name,
                            dict(difference_reason="", difference_notes=""), update_modified=False)
                if doc.doctype == "CN Reconciliation Period":
                    frappe.db.set_value(doc.doctype, doc.name, "status", "Cerrado", update_modified=False)
            manual = next(doc.detail_rows[0] for doc in documents
                          if doc.doctype == "CN Remittance Allocation" and doc.deposit_amount == 10.01)
            frappe.db.set_value(manual.doctype, manual.name, "difference_notes", "Soporte manual", update_modified=False)
            for doc in documents:
                doc.reload()
            before = [financial_state(doc) for doc in documents]
            scopes = {}
            for doc in documents:
                scopes.setdefault(doc.doctype, []).append(frappe._dict(name=doc.name))
            with patch.object(migration, "_pages", side_effect=lambda _query, doctype, **kw: scopes.get(doctype, [])):
                migration.execute()
                comments = frappe.db.count("Comment")
                migration.execute()
                assert frappe.db.count("Comment") == comments
            for doc in documents:
                doc.reload()
            assert before == [financial_state(doc) for doc in documents]
            manual = frappe.get_doc(manual.doctype, manual.name)
            assert manual.difference_notes == "Soporte manual" and not manual.difference_reason
            source = documents[0]
            assert source.rows[0].difference_reason == REASON and source.rows[0].difference_notes == NOTES
            assert operative.collection_rows[0].difference_reason == REASON
            return dict(shortfall_classified=True, surplus_detail_classified=True,
                        collection_quality_classified=True,
                        manual_notes_preserved=True, closed_periods_preserved=True,
                        financial_state_unchanged=True, backfill_idempotent=True)
    finally:
        frappe.db.rollback()
