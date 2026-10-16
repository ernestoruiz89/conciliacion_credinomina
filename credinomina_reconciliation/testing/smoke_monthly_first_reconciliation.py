"""Monthly list, missing-assignment recovery and rollback on a disposable site."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import monthly_first_reconciliation as api


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    tables = ["CN Employer", "CN Client", "CN Reconciliation Period", "CN Collection Row", "CN Accounting Import", "CN Source Row"]
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "MONTH-FIRST-" + frappe.generate_hash(length=8)

            def fixture(label, month=10):
                company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker + label,
                    employer_code=marker + label, payroll_frequency="Mensual")).insert()
                client = frappe.get_doc(dict(doctype="CN Client", employer=company.name,
                    client_name=marker + label, client_number=marker + label)).insert()
                period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                    payroll_month=f"2026-{month:02d}-01", reconciliation_mode="Operativa", collection_cycle="Mensual",
                    collection_rows=[dict(client=client.name, client_name=client.client_name,
                        client_number=client.name, loan_number="9918565-1", expected_usd=50)])).insert()
                return company, client, period

            ready_company, ready_client, ready = fixture("-ready")
            company, client, missing = fixture("-missing")
            _, _, september = fixture("-september", 9)

            def imported(label, owner, person, amount, assigned=""):
                return frappe.get_doc(dict(doctype="CN Accounting Import", employer=owner.name,
                    historical_period=assigned, currency="USD", status="Importado",
                    source_file=f"/private/files/{marker}-{label}.csv", rows=[dict(event_type="Aplicacion",
                        event_date="2026-10-15", currency="USD", amount=amount, amount_usd=amount, effective=1,
                        source_key=f"{marker}-{label}", client=person.name, client_name=person.client_name,
                        client_number=person.name, loan_number="9918565-1")])).insert()

            imported("ready", ready_company, ready_client, 50, ready.name)
            first = imported("first", company, client, 20)
            second = imported("second", company, client, 30)
            month_names = {row.name for row in api.list_periods("2026-10-16")}
            assert ready.name in month_names and missing.name in month_names and september.name not in month_names
            assert api.reconcile_period(ready.name, "2026-10-01")["state"] == "Conforme"
            result = api.reconcile_period(missing.name, "2026-10-01")
            assert result["state"] == "Sin aplicaciones asignadas" and result["can_assign"]
            candidates = api.get_unassigned_imports(missing.name)
            assert {row["name"] for row in candidates["imports"]} == {first.name, second.name}
            selected = [{key: row[key] for key in ("name", "modified", "applications")} for row in candidates["imports"]]
            # A later failure in reconciliation rolls back all earlier assignments
            # exactly as Frappe rolls back an unsuccessful POST transaction.
            frappe.db.savepoint("monthly_assignment")
            with patch.object(api, "reconcile_period", side_effect=frappe.ValidationError("Reconciliation failed")):
                try:
                    api.assign_and_reconcile(missing.name, candidates["modified"], selected)
                except frappe.ValidationError:
                    frappe.db.rollback(save_point="monthly_assignment")
                else:
                    raise AssertionError("Expected failed reconciliation")
            assert not first.reload().historical_period and not first.rows[0].historical_period
            assert not second.reload().historical_period and not second.rows[0].historical_period
            result = api.assign_and_reconcile(missing.name, candidates["modified"], selected)
            assert result["state"] == "Conforme" and result["assigned_now"] == 2, result
            assert missing.reload().applied_usd == 50
            for doc in (first.reload(), second.reload()):
                assert doc.historical_period == missing.name
                assert doc.rows[0].historical_period == missing.name
                assert doc.rows[0].collection_row_id == missing.collection_rows[0].name
            assert not api.get_unassigned_imports(missing.name)["imports"]
            # Repeating a completed month remains deterministic.
            assert api.reconcile_period(missing.name, "2026-10-01")["state"] == "Conforme"
            assert missing.reload().applied_usd == 50
            return dict(all_companies_in_month=True, other_month_excluded=True, missing_assignments_reported=True,
                        assignment_and_reprocess=True, failed_assignment_rolled_back=True, repeat_keeps_totals=True,
                        rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, "El ensayo dejó registros"
