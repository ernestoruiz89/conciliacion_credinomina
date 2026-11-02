"""Real many-to-many settlements and explicit undo, on the disposable site only."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "PRESERVE-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD", historical_backfill=1,
                rows=[dict(event_type="Aplicacion", event_date="2025-04-30", currency="USD", amount=amount,
                    amount_usd=amount, effective=1, historical_period=period.name, source_key=f"{marker}-{index}",
                    client_number=f"{marker}-{index}", loan_number=f"{index}-1") for index, amount in enumerate((100, 80))])).insert()
            engine._reconcile_sources(company.name)
            source.reload()
            applications = [row.name for row in source.rows]

            def deposit(label, portions):
                doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date="2025-04-30", deposit_reference=f"{marker}-{label}", deposit_currency="USD",
                    deposit_amount=sum(portions), targets=[dict(historical_application=name, amount_usd=amount)
                        for name, amount in zip(applications, portions)])).insert()
                doc.submit()
                reconcile_deposit(doc)
                return doc.reload()

            def evidence(doc):
                doc.reload()
                return {field: doc.get(field) for field in ("allocation_detail", "allocated_usd", "unallocated_usd",
                    "unclassified_usd", "justified_surplus_usd", "result", "detail_status")}

            first = deposit("D1", (60, 30))
            original = evidence(first)
            second = deposit("D2", (20, 30))
            assert evidence(first) == original
            source.reload()
            assert [row.historical_remitted_usd for row in source.rows] == [80, 60]
            second_original = evidence(second)
            for index, application in enumerate(applications * 2):
                key = f"{marker}-adjustment-{index}"
                with imported_item(key):
                    adjustment = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name,
                        period=period.name, category="Ajuste de aplicación", review_action="Ajuste de aplicación",
                        posting_date="2025-04-30", currency="USD", amount=10, application_adjustment_usd=10,
                        related_application=application, accounting_source_key=key, source_currency="USD",
                        source_credit=10, source_date="2025-04-30", description=key, review_notes=key)).insert()
                confirm_adjustment(adjustment.name)
                assert evidence(first) == original and evidence(second) == second_original
            # Each application now has two deposits and two confirmed adjustments.
            source.reload()
            assert all(row.historical_balance_usd == 0 for row in source.rows)
            for _ in range(2):
                engine._reconcile_sources(company.name, preserve_deposits=False)
                reconcile_deposit(first.reload())
                reconcile_deposit(second.reload())
                assert evidence(first) == original and evidence(second) == second_original
            # A changed input must not silently withdraw old cash links.
            frappe.db.savepoint("protect_existing_cash")
            first.reload()
            first.targets[0].amount_usd = 50
            first.save()
            try:
                reconcile_deposit(first)
            except frappe.ValidationError as error:
                assert "asignación ya confirmada" in str(error), str(error)
                frappe.db.rollback(save_point="protect_existing_cash")
            else:
                raise AssertionError("A normal reconciliation withdrew a confirmed allocation")
            assert evidence(first) == original
            # Explicit undo removes D2 only, preserving D1 and both adjustments.
            reconcile_deposit(second.reload(), undo=True)
            assert evidence(first) == original
            source.reload()
            assert [row.historical_remitted_usd for row in source.rows] == [60, 30]
            assert [row.application_adjustment_usd for row in source.rows] == [20, 20]
            assert [row.historical_balance_usd for row in source.rows] == [20, 30]
            assert len(json.loads(first.allocation_detail)) == 2
            _operative_anchor(marker)
            _late_fifo_application(marker + "-F")
            return dict(many_deposits_many_applications=True, cash_and_adjustments=True,
                global_recalculation_preserves_links=True, repeated_reconcile_idempotent=True,
                changed_inputs_rejected=True, explicit_undo_isolated=True, operative_quota_preserved=True,
                late_fifo_application_preserves_old_cash=True, rolled_back=True)
    finally:
        frappe.db.rollback()


def _operative_anchor(marker):
    company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-OPER", employer_code=marker + "-O")).insert()
    client = frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_name=marker, client_number=marker)).insert()
    period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
        payroll_month="2026-09-01", reconciliation_mode="Operativa", collection_cycle="Mensual",
        collection_rows=[dict(row_key=f"{marker}-{amount}", client=client.name, client_name=marker,
            client_number=marker, loan_number="12345-1", expected_usd=amount) for amount in (100, 80)])).insert()

    def application(label):
        return frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, historical_period=period.name,
            source_file=f"/private/files/{marker}-{label}.csv", status="Importado", currency="USD",
            rows=[dict(source_key=f"{marker}-{label}", event_type="Aplicacion", event_date="2026-09-30", currency="USD",
                amount=80, amount_usd=80, effective=1, client=client.name, client_number=marker,
                loan_number="12345-1", historical_period=period.name, processing_route="Operativa")])).insert()

    original = application("original")
    engine._reconcile_sources(company.name)
    original.reload()
    before = original.rows[0].application_allocation_detail
    assert json.loads(before)[0]["collection_row_id"] == period.collection_rows[1].name
    newcomer = application("newcomer")
    # A new source can sort first after backfill/import order changes.
    frappe.db.set_value(newcomer.doctype, newcomer.name, "creation", "2020-01-01 00:00:00", update_modified=False)
    engine._reconcile_sources(company.name)
    original.reload(); newcomer.reload()
    assert original.rows[0].application_allocation_detail == before
    assert json.loads(newcomer.rows[0].application_allocation_detail)[0]["collection_row_id"] == period.collection_rows[0].name


def _late_fifo_application(marker):
    company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + "-FIFO", employer_code=marker + "-F")).insert()
    client = frappe.get_doc(dict(doctype="CN Client", employer=company.name, client_name=marker, client_number=marker)).insert()
    period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
        payroll_month="2025-04-01", reconciliation_mode="Historica", historical_scope="Mensual")).insert()

    def application(label, day, amount):
        doc = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, historical_backfill=1,
            source_file=f"/private/files/{marker}-{label}.csv", status="Importado", currency="USD",
            rows=[dict(source_key=f"{marker}-{label}", event_type="Aplicacion", event_date=day, currency="USD",
                amount=amount, amount_usd=amount, effective=1, client=client.name, client_number=marker,
                loan_number="4567-1", historical_period=period.name, processing_route="Historica")])).insert()
        engine._reconcile_sources(company.name)
        return doc.reload()

    def deposit(label, amount):
        doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
            deposit_date="2025-04-30", deposit_reference=f"{marker}-{label}", deposit_currency="USD", deposit_amount=amount,
            apply_fifo=1, detail_periods=[dict(period=period.name)], detail_file=f"/private/files/{marker}.csv",
            detail_source_file=f"/private/files/{marker}.csv", detail_hash=marker,
            detail_rows=[dict(source_row=2, client=client.name, client_number=marker, client_name=marker,
                loan_number="4567-1", deducted_usd=amount)])).insert()
        doc.submit()
        reconcile_deposit(doc)
        return doc.reload()

    already_paid = application("original", "2025-04-30", 40)
    first = deposit("first", 40)
    saved = first.allocation_detail
    earlier = application("earlier", "2025-04-15", 30)
    assert first.reload().allocation_detail == saved
    frappe.db.savepoint("fifo_rematch_rejected")
    try:
        reconcile_deposit(first)
    except frappe.ValidationError as error:
        assert "asignación ya confirmada" in str(error), str(error)
        frappe.db.rollback(save_point="fifo_rematch_rejected")
    else:
        raise AssertionError("FIFO moved a confirmed payment to a newly imported older application")
    second = deposit("second", 30)
    assert first.reload().allocation_detail == saved
    assert json.loads(second.allocation_detail)[0]["aplicacion_id"] == earlier.rows[0].name
    assert already_paid.reload().rows[0].historical_remitted_usd == 40
