"""Measure real manual/FIFO deposit reconciliation and preservation; rollback only."""
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import reconcile_remittance


def run(count=100):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    measurements = []
    original_sql = frappe.db.sql

    def reconcile(document, label):
        queries = 0

        def sql(*args, **kwargs):
            nonlocal queries
            queries += 1
            return original_sql(*args, **kwargs)

        started = perf_counter()
        with patch.object(frappe.db, "sql", new=sql):
            result = reconcile_remittance(document.name)
        measurements.append(dict(stage=label, queries=queries, seconds=round(perf_counter() - started, 3)))
        document.reload()
        assert document.allocated_usd == count * 30, document.allocated_usd
        assert all(row.pending_usd == 0 for row in document.detail_rows)
        assert result["detail_matched"] == count, result
        # Allow framework bookkeeping, while catching per-target query growth.
        if label == "manual":
            assert queries < count * 8 + 200, measurements
        elif label.startswith("repeat"):
            assert queries < count * 4 + 200, measurements
            assert result["saved_imports"] == 0, result

    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "DEP-PERF-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
            clients = [frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_name=marker + str(i), client_number=marker + str(i))).insert() for i in range(count)]
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                source_file=f"/private/files/{marker}.csv", status="Importado", currency="USD", historical_backfill=1,
                rows=[dict(source_row=i + 2, source_key=f"{marker}-{i}", event_type="Aplicacion",
                    event_date="2025-04-30", currency="USD", amount=100, amount_usd=100, effective=1,
                    historical_period=period.name, client=client.name, client_name=client.client_name,
                    client_number=client.name, loan_number=f"{i + 1}-1") for i, client in enumerate(clients)])).insert()
            _reconcile_sources(company.name)
            source.reload()

            def deposit(suffix, manual):
                doc = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_reference=marker + suffix, deposit_date="2025-05-05", deposit_currency="USD",
                    deposit_amount=30 * count, detail_file=f"/private/files/{marker}{suffix}.xlsx",
                    detail_source_file=f"/private/files/{marker}{suffix}.xlsx", detail_hash="TEST", apply_fifo=1,
                    detail_periods=[dict(period=period.name)],
                    detail_rows=[dict(client=client.name, client_name=client.client_name, client_number=client.name,
                        employer=company.name, loan_number=f"{i + 1}-1", deducted_usd=30, amount_usd=30)
                        for i, client in enumerate(clients)])).insert()
                if manual:
                    for detail, application in zip(doc.detail_rows, source.rows):
                        doc.append("targets", dict(detail_row=detail.name,
                            historical_application=application.name, amount_usd=30))
                    doc.save()
                doc.submit()
                return doc

            manual = deposit("MANUAL", True)
            reconcile(manual, "manual")
            original = document_state(manual)
            fifo = deposit("FIFO", False)
            reconcile(fifo, "fifo")
            assert document_state(manual.reload()) == original, "Changed previously reconciled deposit"
            fifo_original = document_state(fifo)
            reconcile(manual, "repeat_manual")
            reconcile(fifo, "repeat_fifo")
            assert document_state(manual) == original
            assert document_state(fifo) == fifo_original
            source.reload()
            period.reload()
            assert all(row.historical_remitted_usd == 60 and row.historical_balance_usd == 40 for row in source.rows)
            assert period.remitted_usd == count * 60 and period.applied_usd == count * 100
            return dict(rows=count, measurements=measurements, existing_links_preserved=True,
                        no_duplicate_cash=True, repeated_reconciliation_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
