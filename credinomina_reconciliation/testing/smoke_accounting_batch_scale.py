"""26,000 synthetic rows through the real bounded importer on the test site."""

import csv
import io
import resource
import time
from datetime import date, timedelta
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation import accounting_batch_store as store
from credinomina_reconciliation.parsers import file_sha256


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "scale-" + frappe.generate_hash(length=8)
    token = source = company = digest = None
    started = time.monotonic()
    try:
        company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert().name
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                         "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
        choices = {}
        for i in range(26000):
            day = str(date(2025, 1, 1) + timedelta(days=i // 100))
            writer.writerow(["123", day, f"{marker}-{i}", str(i), "NOTA AL PRESTAMO PAGO APLICADO " + "EVIDENCIA " * 12,
                             10, 0, "", "Empresa a seleccionar", "", "12", "05"])
            choices[str(i + 2)] = company
        source = save_file(f"{marker}.csv", stream.getvalue().encode(), None, None, is_private=1)
        digest = file_sha256(bulk._file(source.file_url)[1])
        options = {"source_file": source.file_url, "currency": "USD", "employer_assignments": choices,
                   "assignments_file_hash": digest}
        with patch.object(frappe, "enqueue"):
            token = bulk.preview_bulk_import(**options)["token"]
            frappe.db.commit()
            bulk.run_bulk_job(token, "Administrator")
            assert bulk._state(token)["status"] == "Vista previa", bulk._state(token).get("error")
            assert bulk._state(token)["summary"]["rows"] == 26000
            bulk.confirm_bulk_import(token)
            frappe.db.commit()
            bulk.run_bulk_job(token, "Administrator")
            assert bulk._state(token)["total_blocks"] == 26
            get_content = File.get_content
            def no_original_read(self):
                if self.file_url == source.file_url:
                    raise AssertionError("Original re-read during block creation")
                return get_content(self)
            with patch.object(File, "get_content", no_original_read):
                for _ in range(26):
                    bulk.run_bulk_job(token, "Administrator")
                    assert bulk._state(token)["status"] != "Error", bulk._state(token).get("error")
            state = bulk._state(token)
            assert state["status"] == "Completado"
            assert state["created_count"] == 260
            parents = frappe.get_all(bulk.DOCTYPE, filters={"bulk_source_hash": digest}, pluck="name")
            assert len(parents) == 260
            assert frappe.db.count("CN Source Row", {"parent": ["in", parents], "parenttype": bulk.DOCTYPE}) == 26000
            assert state["options"]["employer_assignments"] == choices
        return {"rows": 26000, "documents": 260, "committed_blocks": 26, "original_reads_in_blocks": 0,
                "manual_choices_retained": len(choices), "peak_process_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                "seconds_before_cleanup": round(time.monotonic() - started, 1)}
    finally:
        frappe.db.rollback()
        cleanup_queue = patch.object(frappe, "enqueue")
        cleanup_queue.start()
        if digest:
            for name in frappe.get_all(bulk.DOCTYPE, filters={"bulk_source_hash": digest}, pluck="name"):
                frappe.delete_doc(bulk.DOCTYPE, name, ignore_permissions=True, force=True)
        if token:
            frappe.db.delete(store.BLOCK, {"batch": token})
            frappe.db.delete(store.BATCH, {"name": token})
            frappe.cache.delete_value(bulk._key(token))
        if company and frappe.db.exists("CN Employer", company):
            frappe.delete_doc("CN Employer", company, ignore_permissions=True)
        if source and frappe.db.exists("File", source.name):
            frappe.delete_doc("File", source.name, ignore_permissions=True)
        frappe.db.commit()
        cleanup_queue.stop()
