"""Two real connections attempt to spend the same application; isolated fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import frappe
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    site, sites_path = frappe.local.site, frappe.local.sites_path
    if site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    marker = "CONCURRENT-CASH-" + frappe.generate_hash(length=10)
    names = {}
    stale_ready, committed = Event(), Event()

    def worker(deposit_name, stale):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect()
            frappe.set_user("Administrator")
            deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
            if stale:
                stale_ready.set()
                assert committed.wait(20)
            else:
                assert stale_ready.wait(20)
            try:
                reconcile_deposit(deposit)
                frappe.db.commit()
                assert not stale, "Stale competing deposit was accepted"
                return "First deposit reconciled"
            except frappe.ValidationError as exc:
                assert stale and "Otra operación" in str(exc), str(exc)
                return "Second deposit rejected stale cash state"
        finally:
            if not stale:
                committed.set()
            frappe.db.rollback()
            frappe.destroy()

    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
            names["CN Employer"] = [company.name]
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
                "historical_scope": "Fecha exacta", "historical_application_date": "2025-04-30"}).insert()
            names[period.doctype] = [period.name]
            source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
                "currency": "USD", "source_file": f"/private/files/{marker}.csv", "status": "Importado",
                "rows": [{"event_type": "Aplicacion", "event_date": "2025-04-30", "source_key": marker,
                    "client_name": marker, "client_number": marker, "loan_number": "999990001-1",
                    "currency": "USD", "amount": 100, "amount_usd": 100, "effective": 1,
                    "historical_period": period.name, "processing_route": "Historica"}]}).insert()
            names[source.doctype] = [source.name]
            _reconcile_sources(company.name)
            names["CN Remittance Allocation"] = []
            for index in range(2):
                deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company.name,
                    "deposit_date": "2025-05-10", "deposit_reference": f"{marker}-{index}",
                    "deposit_currency": "USD", "deposit_amount": 100,
                    "targets": [{"historical_application": source.rows[0].name, "amount_usd": 100}],
                    "detail_periods": [{"period": period.name}]}).insert()
                deposit.submit()
                names[deposit.doctype].append(deposit.name)
            frappe.db.commit()
            first, second = names["CN Remittance Allocation"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                stale = pool.submit(worker, second, True)
                winner = pool.submit(worker, first, False)
                results = [winner.result(timeout=30), stale.result(timeout=30)]
            frappe.db.rollback()
            # A real retry must still not allocate a second $100 to this loan.
            reconcile_deposit(frappe.get_doc("CN Remittance Allocation", second))
            rows = frappe.get_all("CN Remittance Allocation", filters={"name": ["in", [first, second]]},
                fields=["allocated_usd", "unclassified_usd"])
            assert sum(row.allocated_usd for row in rows) == 100
            assert sum(row.unclassified_usd for row in rows) == 100
            source.reload()
            assert source.rows[0].historical_remitted_usd == 100
            return {"two_connections": True, "outcomes": results, "fresh_retry_no_double_payment": True,
                "total_deposits": 200, "allocated": 100, "remaining": 100, "fixtures_removed": True}
    finally:
        frappe.db.rollback()
        # Direct scoped deletion is fixture cleanup, not an operational cancellation.
        # Only names created above are eligible; never use this outside this test.
        for doctype, documents in reversed(list(names.items())):
            for field in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(field.options, {"parenttype": doctype, "parent": ["in", documents]})
            frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", documents]})
            frappe.db.delete("Comment", {"reference_doctype": doctype, "reference_name": ["in", documents]})
            frappe.db.delete(doctype, {"name": ["in", documents]})
        frappe.db.commit()
