"""Rollback-only SQL regression of mixed adjustment/cash remainder offsets."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation.complementary_balances import load_balances
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"), \
                patch("frappe.model.rename_doc.sync_user_settings"):
            marker = "REMAINDER-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-08-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                historical_backfill=1, historical_period=period.name, status="Importado", currency="USD",
                source_file=f"/private/files/{marker}.csv", rows=[dict(source_key=marker, event_type="Aplicacion",
                    event_date="2025-08-15", currency="USD", amount=1929.34, amount_usd=1929.34, effective=1,
                    client_number=marker, client_name="Cliente prueba", loan_number=marker,
                    processing_route="Historica", historical_period=period.name)])).insert()
            _reconcile_sources(company.name, preserve_deposits=True)
            from credinomina_reconciliation.complementary_subcategories import seed_subcategories
            seed_subcategories()

            def item(category, action, amount, debit=0, credit=0, **values):
                key = marker + category
                if category == "Ajuste de conciliación":
                    values["subcategory"] = "Otro ajuste sin CxC"
                doc = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name,
                    category=category, review_action=action, amount_reviewed=1, posting_date="2025-11-20",
                    currency="USD", amount=amount, reference=marker, accounting_source_key=key, source_currency="USD",
                    source_account="160209013004", source_debit=debit, source_credit=credit,
                    source_date="2025-11-20", source_client_name="Cliente prueba", description="Prueba remanente",
                    **values))
                with imported_item(key):
                    doc.insert()
                doc.submit()
                return doc.reload()

            adjustment = item("Ajuste de aplicación", "Ajuste de aplicación", 1929.35, credit=1929.35,
                related_application=source.rows[0].name, application_adjustment_usd=1929.34)
            cash = item("Ajuste de conciliación", "Partida de depósito", 47.28, debit=47.28)
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                deposit_reference=marker, deposit_date="2025-09-02", deposit_currency="USD", deposit_amount=47.28,
                targets=[dict(complementary_item=cash.name, amount_usd=47.27)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            old_deposit = document_state(deposit.reload())
            old_source = document_state(source.reload())
            old_period = document_state(period.reload())
            old_ledger = json.loads(deposit.allocation_detail)
            preserved = [dict(category=doc.category, review_action=doc.review_action, review_status=doc.review_status,
                related_application=doc.related_application, related_import=doc.related_import,
                application_adjustment_usd=doc.application_adjustment_usd) for doc in (adjustment, cash)]
            preview = offsets.preview_compensation(adjustment.name, cash.name)
            assert money(preview["suggested_usd"]) == money(.01), preview
            args = (adjustment.name, cash.name, .01, "2025-11-21", "Compensar únicamente remanentes", "a" * 32)
            try:
                offsets.confirm_compensation(adjustment.name, cash.name, .02, "2025-11-21", "Exceso", "b" * 32)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Se compensó más que el remanente")
            # A failure in the second half must undo the first parent and its Version.
            cls = type(cash)
            save = cls.save
            count = frappe.db.count("CN Complementary Offset")
            versions = frappe.db.count("Version")
            def fail_second(doc, *a, **kw):
                if doc.name == sorted([adjustment.name, cash.name])[1]:
                    raise RuntimeError("Injected second-parent failure")
                return save(doc, *a, **kw)
            with patch.object(cls, "save", fail_second):
                try:
                    offsets.confirm_compensation(*args)
                except RuntimeError as error:
                    assert "Injected" in str(error)
                else:
                    raise AssertionError("Failure not injected")
            assert frappe.db.count("CN Complementary Offset") == count
            assert frappe.db.count("Version") == versions
            offsets.confirm_compensation(*args)
            offsets.confirm_compensation(*args)

            def check(pending, compensated):
                for doc, before in zip((adjustment, cash), preserved):
                    doc.reload()
                    assert all(doc.get(field) == value for field, value in before.items()), doc.as_dict()
                    value = load_balances([doc])[doc.name]
                    assert money(value["pending_usd"]) == money(pending), value
                    assert money(doc.compensated_usd) == money(compensated)
                assert document_state(source.reload()) == old_source
                assert document_state(period.reload()) == old_period
                assert document_state(deposit.reload()) == old_deposit
                assert json.loads(deposit.allocation_detail) == old_ledger
            check(0, .01)
            assert len(adjustment.compensations) == len(cash.compensations) == 1
            # Repeated reconciliation sees only US$47.27 cash capacity, retaining every confirmed link.
            for _ in range(2):
                reconcile_deposit(deposit)
                check(0, .01)
            assert money(offsets.balance(cash, "2025-11-20")["pending_usd"]) == money(.01)
            assert money(offsets.balance(adjustment, "2025-11-21")["pending_usd"]) == 0
            offsets.reverse_compensation(adjustment.name, "a" * 32, "2025-12-01", "Prueba reversión", "c" * 32)
            check(.01, 0)
            offsets.correct_compensation_date(adjustment.name, "a" * 32, "2025-11-22", "Prueba fecha", "d" * 32)
            check(0, .01)
            return dict(offset_each_usd=.01, adjustment_preserved_usd=1929.34, deposit_preserved_usd=47.27,
                        repeated_without_duplicates=True, reversal_and_date_verified=True,
                        second_parent_failure_rolled_back=True, rolled_back=True)
    finally:
        frappe.db.rollback()
