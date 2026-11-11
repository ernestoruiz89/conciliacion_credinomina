"""Measure loading payroll applications and protect paid reimports; rollback only."""
import io
from time import perf_counter
from unittest.mock import patch

import frappe
from openpyxl import Workbook

from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.accounting_reimport_state import STATE_FIELDS, TEXT_FIELDS
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api


def run(count=200):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    measurements = []
    original_sql = frappe.db.sql
    original_compare = api.application_matches_collection
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "LOAD-PERF-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            clients = [frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_name=marker + str(i), client_number=marker + str(i))).insert() for i in range(count)]
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2026-09-01", reconciliation_mode="Operativa", collection_cycle="Mensual",
                collection_rows=[dict(client=client.name, client_name=client.client_name,
                    loan_number=f"{i + 1:06}-1", row_key=marker + str(i), expected_usd=100)
                    for i, client in enumerate(clients)])).insert()
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                          "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
            for i, client in enumerate(clients):
                sheet.append(["123", "2026-09-30", marker + str(i), marker + str(i),
                    f"NOTA AL PRESTAMO {i + 1}-1 PAGO APLICADO", 100, 0, f"{i + 1}-1",
                    marker, client.client_name, "12", "05"])

            def content():
                stream = io.BytesIO()
                workbook.save(stream)
                return stream.getvalue()

            file_content = content()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                currency="USD", historical_period=period.name, source_file=f"/private/files/{marker}.xlsx")).insert()

            def load(label):
                queries = comparisons = 0

                def sql(*args, **kwargs):
                    nonlocal queries
                    queries += 1
                    return original_sql(*args, **kwargs)

                def compare(*args, **kwargs):
                    nonlocal comparisons
                    comparisons += 1
                    return original_compare(*args, **kwargs)

                started = perf_counter()
                with patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="movements.xlsx"), file_content)), \
                     patch.object(frappe.db, "sql", new=sql), \
                     patch.object(api, "application_matches_collection", new=compare):
                    result = api.import_source_file(source.name)
                measurements.append(dict(stage=label, queries=queries, comparisons=comparisons,
                    seconds=round(perf_counter() - started, 3)))
                source.name = result["import_name"]
                source.reload()
                assert result["rows"] == count, result
                assert comparisons <= count, measurements
                return result

            load("first")
            names = [row.name for row in source.rows]
            assert [row.collection_row_id for row in source.rows] == [row.name for row in period.collection_rows]
            assert all(row.historical_remitted_usd == 0 and row.historical_balance_usd == 100 for row in source.rows)
            deposits = []
            for index, amount in enumerate((40, 20)):
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date="2026-09-30", deposit_reference=marker + str(index), deposit_currency="USD",
                    deposit_amount=count * amount, targets=[dict(historical_application=name, amount_usd=amount)
                        for name in names])).insert()
                deposit.submit()
                reconcile_deposit(deposit)
                deposits.append(deposit)
            key = marker + "-adjustment"
            with imported_item(key):
                adjustment = frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name,
                    period=period.name, category="Ajuste de aplicación", review_action="Ajuste de aplicación",
                    posting_date="2026-09-30", currency="USD", amount=10, application_adjustment_usd=10,
                    related_application=names[0], accounting_source_key=key, source_currency="USD",
                    source_credit=10, source_date="2026-09-30", description=key, review_notes=key)).insert()
            confirm_adjustment(adjustment.name)
            source.reload()
            source.rows[0].difference_notes = "Revisión documentada del cliente"
            source.save()
            originals = {doc.name: document_state(doc.reload()) for doc in [*deposits, adjustment, period]}
            def row_state(row):
                # Compare financial/identity evidence, independently of the
                # child editor/time and UI defaults refreshed by Frappe.
                fields = (*STATE_FIELDS, *TEXT_FIELDS, "name", "client", "event_date", "amount", "amount_usd",
                          "equivalent_amount", "source_debit", "source_credit", "manual_fx_rate", "fx_rate",
                          "difference_reason", "difference_notes")
                return {key: row.get(key) for key in fields}

            original_rows = [row_state(row) for row in source.reload().rows]

            result = load("reimport_with_deposits_and_adjustment")
            assert result["saved_imports"] == 0, result
            assert [row.name for row in source.rows] == names
            for row, before in zip(source.rows, original_rows):
                after = row_state(row)
                assert after == before, {key: (before.get(key), value)
                                         for key, value in after.items() if before.get(key) != value}
            for doc in [*deposits, adjustment, period]:
                assert document_state(doc.reload()) == originals[doc.name], doc.name
            assert all(row.historical_remitted_usd == 60 for row in source.rows)
            assert [row.historical_balance_usd for row in source.rows] == [30] + [40] * (count - 1)
            assert source.rows[0].application_adjustment_usd == 10
            assert source.rows[0].difference_notes == "Revisión documentada del cliente"

            # Reloading a changed file must still reject removing a paid row.
            original_import = document_state(source)
            sheet.delete_rows(2)
            file_content = content()
            frappe.db.savepoint("reject_paid_row_removal")
            try:
                load("invalid_removal")
            except frappe.ValidationError as error:
                assert "ya tiene vínculos de conciliación" in str(error), str(error)
            else:
                raise AssertionError("Removed a paid application")
            finally:
                frappe.db.rollback(save_point="reject_paid_row_removal")
            assert document_state(source.reload()) == original_import
            for doc in [*deposits, adjustment, period]:
                assert document_state(doc.reload()) == originals[doc.name], doc.name
            return dict(rows=count, measurements=measurements, row_ids_and_links_preserved=True,
                        deposits_and_adjustment_preserved=True, paid_row_removal_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
