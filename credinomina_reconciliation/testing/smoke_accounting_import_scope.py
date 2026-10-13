"""Source reloads preserve other imports, real cash and unassignment semantics."""
import io
from unittest.mock import patch

import frappe
from openpyxl import Workbook

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "IMPORT-SCOPE-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Quincenal")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", client_name=marker,
                client_number=marker, employer=employer.name)).insert()
            old_period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica")).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2026-09-01", reconciliation_mode="Operativa", collection_cycle="Segunda quincena",
                collection_rows=[dict(client=client.name, client_name=marker, loan_number=loan,
                    row_key=marker + loan, expected_usd=amount) for loan, amount in [("991855-1", 100), ("991856-1", 50)]]
            )).insert()
            others, deposits = [], []
            for index, target in enumerate((old_period, period)):
                source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                    historical_period=target.name, currency="USD", status="Importado",
                    source_file=f"/private/files/{marker}-{index}.csv", rows=[dict(event_type="Aplicacion",
                        event_date="2025-07-15" if index == 0 else "2026-09-30", currency="USD", amount=50,
                        amount_usd=50, effective=1, historical_period=target.name, source_key=marker + str(index),
                        client=client.name, client_name=marker, client_number=marker, loan_number="991856-1")])).insert()
                api._reconcile_sources(employer.name, preserve_deposits=True, period_name=target.name)
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                    deposit_date="2026-09-30", deposit_reference=marker + str(index), deposit_currency="USD",
                    deposit_amount=50, targets=[dict(historical_application=source.rows[0].name, amount_usd=50)])).insert()
                deposit.submit()
                reconcile_deposit(deposit)
                assert deposit.reload().allocated_usd == 50
                others.append(source)
                deposits.append(deposit)
            draft = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2026-09-04", deposit_reference=marker + "-draft", deposit_currency="USD",
                deposit_amount=697.91)).insert()
            # Legacy default inherited from the header triggers the old global
            # path's cash guard, despite the imported row having no paid cash.
            frappe.db.set_value("CN Source Row", others[0].rows[0].name, "historical_period", "", update_modified=False)
            snapshots = [(doc, document_state(doc.reload())) for doc in [old_period, *others, *deposits, draft]]
            frappe.db.savepoint("global_repro")
            try:
                api._reconcile_sources(employer.name, preserve_deposits=True)
            except frappe.ValidationError as exc:
                assert "dinero asignado" in str(exc), exc
            else:
                raise AssertionError("The original global failure was not reproduced")
            finally:
                frappe.db.rollback(save_point="global_repro")
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                          "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
            sheet.append(["123", "2026-09-30", marker, marker, "NOTA AL PRESTAMO 991855-1 PAGO APLICADO",
                          100, 0, "991855-1", marker, marker, "12", "05"])
            stream = io.BytesIO()
            workbook.save(stream)
            current = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                currency="USD", historical_period=period.name, source_file=f"/private/files/{marker}-new.xlsx")).insert()

            def reload_file():
                with patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="movements.xlsx"), stream.getvalue())), \
                     patch.object(api, "load_scoped_imports", side_effect=AssertionError("Loaded company history")), \
                     patch.object(api, "_load_open_periods", side_effect=AssertionError("Loaded unrelated periods")):
                    result = api.import_source_file(current.name)
                current.name = result["import_name"]
                current.reload()
                assert result["imports"] == result["rows"] == 1, result
                for doc, before in snapshots:
                    assert document_state(doc.reload()) == before, doc.name
                return result

            reload_file()
            row_id = current.rows[0].name
            assert period.reload().applied_usd == 150 and period.remitted_usd == 50
            assert current.rows[0].historical_remitted_usd == 0
            # A stale collection total must not become fictitious reserved cash.
            collection = next(row for row in period.collection_rows if row.loan_number == "991855-1")
            frappe.db.set_value("CN Collection Row", collection.name, "applied_usd", 0, update_modified=False)
            reload_file()
            assert current.rows[0].name == row_id and period.reload().applied_usd == 150
            # Clearing the default removes the current source only; fixed paid
            # applications in this same period still contribute their balances.
            current.historical_period = ""
            current.save()
            for _ in range(2):
                reload_file()
                assert current.rows[0].name == row_id and not current.rows[0].historical_period
                assert not current.rows[0].collection_row_id
                assert period.reload().applied_usd == period.remitted_usd == 50
            current.historical_period = period.name
            current.save()
            reload_file()
            assert period.reload().applied_usd == 150 and period.remitted_usd == 50
            return dict(global_error_reproduced=True, reload_scoped=True, stale_unpaid_balance_allowed=True,
                fixed_paid_import_preserved=True, unassignment_and_reassignment=True,
                draft_and_confirmed_deposits_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
