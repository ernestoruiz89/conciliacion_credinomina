"""Loading payroll must not rewrite another period's paid applications."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "COL-SCOPE-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Quincenal")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", client_name=marker,
                client_number=marker, employer=employer.name)).insert()
            old_period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                historical_period=old_period.name, historical_backfill=1, currency="USD", status="Importado",
                source_file=f"/private/files/{marker}-old.csv", rows=[dict(event_type="Aplicacion",
                    event_date="2025-07-15", currency="USD", amount=50, amount_usd=50, effective=1,
                    historical_period=old_period.name, source_key=marker, client=client.name,
                    client_name=marker, client_number=marker, loan_number="9918565-1")])).insert()
            engine._reconcile_sources(employer.name, preserve_deposits=True)
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2025-07-30", deposit_reference=marker, deposit_currency="USD", deposit_amount=50,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=50)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            assert deposit.reload().allocated_usd == 50
            # A legacy application inherits its period from the import header.
            # A company-wide pass copies that default into the paid child and
            # trips the cash guard, although the new period has no money.
            frappe.db.set_value("CN Source Row", source.rows[0].name, "historical_period", "", update_modified=False)
            old_state = document_state(old_period.reload())
            source_state = document_state(source.reload())
            cash_state = document_state(deposit)
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2026-09-01", reconciliation_mode="Operativa",
                collection_cycle="Segunda quincena", cutoff_date="2026-09-30")).insert()
            content = ("Nro. Cliente,Nombre y Apellidos del Cliente,Nro. Crédito,Monto de la cuota en US$\n"
                       f"{marker},{marker},9918565-1,80\n").encode()
            with patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="cobranza.csv"), content)):
                result = api.import_collection(period.name)
                assert result["rows"] == 1, result
                assert result["source_reconciliation"]["imports"] == 0, result
                assert result["source_reconciliation"]["period"] == period.name, result
                period.reload()
                assert period.expected_usd == 80 and period.applied_usd == period.remitted_usd == 0
                row_id = period.collection_rows[0].name
                # Repeating the identical upload takes the early-return branch.
                assert api.import_collection(period.name)["unchanged"]
                assert period.reload().collection_rows[0].name == row_id
            assert document_state(old_period.reload()) == old_state
            assert document_state(source.reload()) == source_state
            assert document_state(deposit.reload()) == cash_state
            # Explicitly assigned applications still reconcile when payroll arrives.
            current = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                historical_period=period.name, currency="USD", status="Importado",
                source_file=f"/private/files/{marker}-current.csv", rows=[dict(event_type="Aplicacion",
                    event_date="2026-09-30", currency="USD", amount=80, amount_usd=80, effective=1,
                    historical_period=period.name, source_key=marker + "-current", client=client.name,
                    client_name=marker, client_number=marker, loan_number="9918565-1")])).insert()
            with patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="cobranza.csv"), content)):
                result = api.import_collection(period.name)
            assert result["unchanged"] and result["source_reconciliation"]["rows"] == 1, result
            assert current.reload().rows[0].collection_row_id == row_id
            assert period.reload().applied_usd == 80 and period.remitted_usd == 0
            current_deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2026-09-30", deposit_reference=marker + "-current", deposit_currency="USD",
                deposit_amount=80, detail_periods=[dict(period=period.name)],
                detail_file=f"/private/files/{marker}.xlsx", detail_source_file=f"/private/files/{marker}.xlsx",
                detail_hash=marker, detail_rows=[dict(client=client.name, client_number=marker,
                    client_name=marker, loan_number="9918565-1", deducted_usd=80, source_row=2)])).insert()
            current_deposit.submit()
            reconcile_deposit(current_deposit)
            assert current_deposit.reload().allocated_usd == 80
            current_cash_state = document_state(current_deposit)
            corrected = content.replace(b",80\n", b",90\n")
            with patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="cobranza.csv"), corrected)):
                result = api.import_collection(period.name)
                assert not result.get("unchanged"), result
                assert api.import_collection(period.name)["unchanged"]
            period.reload()
            assert period.collection_rows[0].name == row_id and period.expected_usd == 90
            assert period.applied_usd == period.remitted_usd == 80
            assert document_state(current_deposit.reload()) == current_cash_state
            assert document_state(old_period.reload()) == old_state
            assert document_state(source.reload()) == source_state
            assert document_state(deposit.reload()) == cash_state
            return dict(empty_period_loaded=True, repeat_upload_scoped=True, assigned_application_matched=True,
                paid_collection_corrected=True, other_paid_period_unchanged=True, deposits_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
