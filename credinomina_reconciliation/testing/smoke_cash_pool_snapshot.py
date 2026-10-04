"""Real two-connection REPEATABLE READ test; only a disposable employer is committed."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import frappe
from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool


def run():
    site, sites_path = frappe.local.site, frappe.local.sites_path
    if site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "CONCURRENCY-" + frappe.generate_hash(length=10)
    with patch.object(frappe, "enqueue"):
        company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
    name = company.name
    frappe.db.commit()
    read_started, writer_done = Event(), Event()

    def worker(stale):
        frappe.init(site=site, sites_path=sites_path)
        try:
            frappe.connect()
            frappe.set_user("Administrator")
            if stale:
                frappe.db.sql("select reconciliation_revision from `tabCN Employer` where name=%s", name)
                read_started.set()
                if not writer_done.wait(15):
                    raise AssertionError("Writer did not complete")
                try:
                    lock_cash_pool([name])
                except frappe.ValidationError as exc:
                    assert "Otra operación" in str(exc)
                    return "stale snapshot rejected"
                raise AssertionError("Stale cash snapshot was accepted")
            if not read_started.wait(15):
                raise AssertionError("Reader did not start")
            lock_cash_pool([name])
            frappe.db.commit()
            return "writer committed"
        finally:
            if not stale:
                writer_done.set()
            frappe.db.rollback()
            frappe.destroy()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(worker, True)
            writer = pool.submit(worker, False)
            results = [reader.result(timeout=25), writer.result(timeout=25)]
        # A fresh transaction can safely retry after the competing writer.
        frappe.db.rollback()
        lock_cash_pool([name])
        frappe.db.rollback()
        return {"two_connections": True, "results": results, "fresh_retry": True, "no_financial_records_created": True}
    finally:
        frappe.db.rollback()
        with patch.object(frappe, "enqueue"):
            frappe.delete_doc("CN Employer", name, ignore_permissions=True)
        frappe.db.commit()
