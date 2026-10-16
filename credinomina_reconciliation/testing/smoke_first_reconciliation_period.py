"""Reject stale cross-period payroll matches on the disposable site; rollback."""
import json
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
    tables = ["CN Employer", "CN Client", "CN Reconciliation Period", "CN Collection Row",
              "CN Accounting Import", "CN Source Row", "CN Remittance Allocation"]
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "FIRST-PERIOD-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                employer_code=marker, payroll_frequency="Quincenal")).insert()
            client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                client_name=marker, client_number=marker)).insert()

            def period(month):
                return frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                    payroll_month=f"2026-{month:02d}-01", reconciliation_mode="Operativa",
                    collection_cycle="Primera quincena", collection_rows=[dict(client=client.name,
                        client_name=marker, client_number=marker, loan_number="9918565-1", expected_usd=50)])).insert()

            september, october = period(9), period(10)

            def imported(label, dates, assigned=""):
                return frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                    historical_period=assigned, currency="USD", status="Importado",
                    source_file=f"/private/files/{marker}-{label}.csv", rows=[dict(event_type="Aplicacion",
                        event_date=date, currency="USD", amount=50, amount_usd=50, effective=1,
                        source_key=f"{marker}-{label}-{index}", client=client.name, client_name=marker,
                        client_number=marker, loan_number="9918565-1") for index, date in enumerate(dates)])).insert()

            older = imported("assigned-september", ["2026-09-15"], september.name)
            current = imported("assigned-october", ["2026-10-15"], october.name)
            missing = imported("unassigned", ["2026-09-15", "2026-09-30"])
            engine._reconcile_sources(company.name, preserve_deposits=True)
            older.reload(); current.reload()

            def paid(source, date, label):
                deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                    deposit_date=date, deposit_reference=f"{marker}-{label}", deposit_currency="USD",
                    deposit_amount=50, targets=[dict(historical_application=source.rows[0].name, amount_usd=50)])).insert().submit()
                reconcile_deposit(deposit)
                assert deposit.reload().allocated_usd == 50
                return deposit

            september_cash = paid(older, "2026-09-15", "old-cash")
            october_cash = paid(current, "2026-10-15", "current-cash")
            untouched = {"period": document_state(september.reload()), "import": document_state(older.reload()),
                         "old_cash": document_state(september_cash), "current_cash": document_state(october_cash)}
            # Reproduce already-persisted bad matches from the old company-wide action.
            for row in missing.rows:
                frappe.db.set_value("CN Source Row", row.name, dict(match_status="Conciliado",
                    collection_period=october.name, collection_row_id=october.collection_rows[0].name,
                    application_allocation_detail=json.dumps([dict(period=october.name,
                        collection_row_id=october.collection_rows[0].name, amount_usd=50)])), update_modified=False)
            frappe.db.set_value("CN Collection Row", october.collection_rows[0].name, "applied_usd", 150, update_modified=False)
            frappe.db.set_value(october.doctype, october.name, "applied_usd", 150, update_modified=False)
            result = api.reconcile_first(october.name)
            october.reload(); current.reload(); missing.reload()
            assert result["period"] == october.name and result["rows"] == 3, result
            assert october.applied_usd == october.applied_total_usd == 50
            assert october.collection_rows[0].applied_usd == 50
            assert current.rows[0].collection_row_id == october.collection_rows[0].name
            assert all(not row.historical_period and not row.collection_period and not row.collection_row_id
                       and json.loads(row.application_allocation_detail) == [] and row.match_status == "Sin coincidencia"
                       for row in missing.rows)
            assert document_state(september.reload()) == untouched["period"]
            assert document_state(older.reload()) == untouched["import"]
            assert document_state(september_cash.reload()) == untouched["old_cash"]
            assert document_state(october_cash.reload()) == untouched["current_cash"]
            # A later company-wide action must not recreate either bad match.
            engine._reconcile_sources(company.name, preserve_deposits=True)
            assert october.reload().applied_usd == 50
            assert all(not row.collection_row_id for row in missing.reload().rows)
            return dict(same_period_only=True, stale_unassigned_links_removed=True, other_period_untouched=True,
                        bank_distributions_preserved=True, company_pass_does_not_relink=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
