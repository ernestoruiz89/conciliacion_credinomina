"""Historical deposit -> closed period -> status repair, with rollback."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import historical_status_repair as repair
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "HIST-STATUS-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Mensual")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=employer.name,
                client_number=marker, client_name=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
                payroll_month="2025-07-01", reconciliation_mode="Historica",
                historical_scope="Fecha exacta", historical_application_date="2025-07-15")).insert()
            source = frappe.get_doc(dict(doctype="CN Accounting Import", employer=employer.name,
                source_file=f"/private/files/{marker}.csv", historical_backfill=1,
                historical_period=period.name, status="Importado", currency="USD", rows=[dict(
                    event_type="Aplicacion", event_date="2025-07-15", source_key=marker, effective=1,
                    client=client.name, client_number=marker, client_name=marker, loan_number="109226-1",
                    historical_period=period.name, currency="USD", amount=123.55, amount_usd=123.55)])).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=employer.name,
                deposit_date="2025-07-15", deposit_reference=marker, deposit_voucher=marker,
                deposit_currency="USD", deposit_amount=123.55, detail_periods=[dict(period=period.name)],
                detail_file=f"/private/files/{marker}.xlsx", detail_source_file=f"/private/files/{marker}.xlsx",
                detail_hash=marker, detail_rows=[dict(client=client.name, client_number=marker,
                    client_name=marker, loan_number="109226-1", deducted_usd=123.55, source_row=2)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            source.reload()
            assert source.status == "Importado" and source.exception_count == 0
            assert source.rows[0].deposit_match_status == "Depósito conciliado"
            # The company-level reconciliation has the same operative pass.
            _reconcile_sources(employer.name, preserve_deposits=True)
            source.reload()
            assert source.rows[0].deposit_match_status == "Depósito conciliado"
            close_period(period.name)
            period.reload(); deposit.reload(); source.reload()
            period_before, deposit_before = period.as_dict(), deposit.as_dict()
            source_before = source.as_dict()
            frappe.db.set_value("CN Source Row", source.rows[0].name, dict(
                deposit_match_status="Sin deposito", deposit_match_reason=repair.OVERWRITTEN_REASON), update_modified=False)
            frappe.db.set_value(source.doctype, source.name, dict(status="Importado con excepciones",
                matched_count=0, exception_count=1), update_modified=False)
            real_get_all = frappe.get_all

            def scoped(doctype, *args, **kwargs):
                if doctype == "CN Source Row":
                    kwargs["filters"] = {**kwargs.get("filters", {}), "parent": source.name}
                return real_get_all(doctype, *args, **kwargs)

            with patch.object(repair.frappe, "get_all", side_effect=scoped):
                assert repair.repair_historical_statuses()["rows_repaired"] == 1
                source.reload()
                assert source.status == "Importado con excepciones"
                result = repair.repair_historical_statuses(False)
                assert result["rows_repaired"] == 1 and not result["unverified_rows"], result
                source.reload()
                repaired = source.as_dict()
                assert repaired.status == "Importado" and repaired.exception_count == 0
                assert repaired.rows[0].deposit_match_status == "Depósito conciliado"
                # Only the explanatory reason differs from the original result.
                source_before.rows[0].deposit_match_reason = repaired.rows[0].deposit_match_reason
                assert repaired == source_before
                assert repair.repair_historical_statuses(False)["rows_repaired"] == 0
                assert frappe.get_doc(source.doctype, source.name).as_dict() == repaired
            assert frappe.get_doc(period.doctype, period.name).as_dict() == period_before
            assert frappe.get_doc(deposit.doctype, deposit.name).as_dict() == deposit_before
            return dict(historical_deposit_status_preserved=True, company_reconciliation_preserved=True,
                closed_period_unchanged=True, deposit_unchanged=True, repair_idempotent=True, rolled_back=True)
    finally:
        frappe.db.rollback()
