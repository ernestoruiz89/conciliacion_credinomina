"""Compare first period assignment with per-row guards; rollback all fixtures."""
import io
from contextlib import nullcontext
from time import perf_counter
from unittest.mock import patch

import frappe
from openpyxl import Workbook

from credinomina_reconciliation import accounting_cash_guard as guard
from credinomina_reconciliation.accounting_reimport_state import STATE_FIELDS, TEXT_FIELDS
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api


def run(count=542):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "FIRST-LOAD-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=employer.name,
                client_name=marker, client_number=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-09-01", reconciliation_mode="Historica")).insert()
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                          "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
            for i in range(count):
                sheet.append(["123", "2025-09-15", marker + str(i), marker + str(i),
                    f"NOTA AL PRESTAMO {i + 1}-1 PAGO APLICADO", 100, 0, f"{i + 1}-1",
                    marker, client.client_name, "12", "05"])
            stream = io.BytesIO()
            workbook.save(stream)
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                currency="USD", source_file=f"/private/files/{marker}.xlsx")).insert()
            measurements = []

            def load():
                with patch.object(api, "_attached_file", return_value=(
                        frappe._dict(file_name="movements.xlsx"), stream.getvalue())):
                    result = api.import_source_file(source.name)
                source.name = result["import_name"]
                source.reload()
                return result

            load()
            names = [row.name for row in source.rows]
            assert len(names) == count and all(not row.historical_period for row in source.rows)
            original_sql = frappe.db.sql
            original_coverage = guard.cash_coverage

            def assign(baseline):
                queries = individual_checks = 0
                def sql(*args, **kwargs):
                    nonlocal queries
                    queries += 1
                    return original_sql(*args, **kwargs)
                def coverage(*args, **kwargs):
                    nonlocal individual_checks
                    individual_checks += 1
                    return original_coverage(*args, **kwargs)
                started = perf_counter()
                with patch.object(frappe.db, "sql", side_effect=sql), \
                     patch.object(guard, "cash_coverage", side_effect=coverage), \
                     (patch.object(guard, "_applications_without_cash", return_value=set()) if baseline else nullcontext()):
                    source.reload()
                    source.historical_period = period.name
                    source.save()
                    result = load()
                measurements.append(dict(mode="before" if baseline else "after", queries=queries,
                    individual_checks=individual_checks, seconds=round(perf_counter() - started, 3)))
                assert result["rows"] == count
                assert [row.name for row in source.rows] == names
                assert all(row.historical_period == period.name and row.match_status == "Conciliado"
                           and row.historical_remitted_usd == 0 and row.historical_balance_usd == 100 for row in source.rows)
                fields = (*TEXT_FIELDS, *STATE_FIELDS, "name", "amount", "amount_usd", "difference_reason", "difference_notes")
                return ([{field: row.get(field) for field in fields} for row in source.rows],
                        {field: period.reload().get(field) for field in ("status", "applied_usd", "remitted_usd",
                            "pending_usd", "deposit_coverage_usd", "complementary_coverage_usd", "historical_fingerprint")})

            frappe.db.savepoint("first_period_baseline")
            before = assign(True)
            frappe.db.rollback(save_point="first_period_baseline")
            after = assign(False)
            assert after == before, "First assignment changed financial results"
            assert measurements[0]["individual_checks"] >= count
            assert measurements[1]["individual_checks"] == 0
            assert measurements[1]["queries"] < measurements[0]["queries"] - count

            # Unconfirmed targets reserve capacity even with zero stored cash.
            draft = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2025-09-15", deposit_reference=marker, deposit_currency="USD", deposit_amount=20,
                targets=[dict(historical_application=names[-1], amount_usd=20)])).insert()
            def reject_reassignment():
                previous = document_state(source.reload())
                deposits = {doc.name: document_state(doc.reload()) for doc in (draft,)}
                frappe.db.savepoint("reject_first_assignment_changes")
                try:
                    source.historical_period = ""
                    source.save()
                except frappe.ValidationError as error:
                    assert "dinero asignado o destinos reservados" in str(error), error
                else:
                    raise AssertionError("Protected period reassignment was accepted")
                finally:
                    frappe.db.rollback(save_point="reject_first_assignment_changes")
                assert document_state(source.reload()) == previous
                assert document_state(draft.reload()) == deposits[draft.name]
            reject_reassignment()
            draft.submit()
            reconcile_deposit(draft)
            assert draft.reload().allocated_usd == 20
            reject_reassignment()
            return dict(rows=count, measurements=measurements, identical_row_ids_and_financial_results=True,
                        draft_reservations_protected=True, confirmed_cash_protected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
