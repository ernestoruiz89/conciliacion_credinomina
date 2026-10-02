"""Real commits, rollback of an interrupted block, durable resume and cleanup.

Only the isolated test site is allowed. Removes only documents tied to this
run's exact batch/source/company; never runs on production.
"""

import csv
import io
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
    marker = "resume-" + frappe.generate_hash(length=8)
    companies, token, source, digest = [], None, None, None
    try:
        companies = [frappe.get_doc({"doctype": "CN Employer", "employer_name": f"{marker}-{i}",
                      "employer_code": f"{marker}-{i}"}).insert().name for i in range(2)]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                         "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
        choices = {}
        for i in range(60):
            writer.writerow(["123", str(date(2025, 4, 1) + timedelta(days=i)), f"{marker}-{i}",
                             f"REF-{i}", f"NOTA AL PRESTAMO {marker}{i}-1 PAGO APLICADO", 10, 0,
                             f"{marker}{i}-1", "Sin empresa en contabilidad", f"Cliente {marker}-{i}", "12", "05"])
            choices[str(i + 2)] = companies[i % 2]
        source = save_file(f"{marker}.csv", stream.getvalue().encode(), None, None, is_private=1)
        digest = file_sha256(bulk._file(source.file_url)[1])
        options = {"source_file": source.file_url, "currency": "USD",
                   "employer_assignments": choices, "assignments_file_hash": digest}
        with patch.object(frappe, "enqueue"), patch("frappe.utils.background_jobs.get_job", return_value=None):
            token = bulk.preview_bulk_import(**options)["token"]
            frappe.db.commit()
            bulk.run_bulk_job(token, "Administrator")
            assert bulk._state(token)["status"] == "Vista previa", bulk._state(token)
            assert len(bulk._state(token)["summary"]["groups"]) == 60
            bulk.save_bulk_employer_choices(token, frappe.as_json(choices), digest)
            frappe.db.commit()
            frappe.cache.delete_value(bulk._key(token))
            assert bulk.latest_bulk_import()["token"] == token
            assert bulk.get_bulk_import_status(token)["draft_assignments"] == choices
            bulk.confirm_bulk_import(token)
            frappe.db.commit()
            bulk.run_bulk_job(token, "Administrator")  # Freeze plan + individual CSVs.
            assert bulk._state(token)["total_blocks"] == 3

            get_content = File.get_content
            def prohibit_original_reads(self):
                if self.file_url == source.file_url:
                    raise AssertionError("Original blob must not be read by any creation block")
                return get_content(self)

            with patch.object(File, "get_content", prohibit_original_reads):
                bulk.run_bulk_job(token, "Administrator")
                assert bulk._state(token)["completed_blocks"] == 1
                assert frappe.db.count(bulk.DOCTYPE, {"bulk_source_hash": digest}) == 25
                original_create = bulk._create_imports
                def fail_mid_block(plan, opts):
                    original_create({**plan, "groups": plan["groups"][:1]}, opts)
                    raise RuntimeError("Synthetic interruption after one uncommitted document")
                with patch.object(bulk, "_create_imports", fail_mid_block), patch.object(File, "create_attachment_copy", None):
                    bulk.run_bulk_job(token, "Administrator")
                assert bulk._state(token)["status"] == "Error"
                assert bulk._state(token)["completed_blocks"] == 1
                assert frappe.db.count(bulk.DOCTYPE, {"bulk_source_hash": digest}) == 25
                old_attempt = bulk._state(token)["job_id"]
                assert bulk.get_bulk_import_status(token)["resumable"]
                bulk.resume_bulk_import(token)
                frappe.db.commit()
                bulk.run_bulk_job(token, "Administrator", attempt=old_attempt)
                assert bulk._state(token)["completed_blocks"] == 1, "Old queued delivery must not advance"
                def fail_after_commit(plan, opts):
                    created = original_create(plan, opts)
                    def lost_queue_delivery():
                        raise RuntimeError("Synthetic queue delivery failure after DB commit")
                    frappe.db.after_commit.add(lost_queue_delivery)
                    return created
                with patch.object(bulk, "_create_imports", fail_after_commit):
                    bulk.run_bulk_job(token, "Administrator")
                assert bulk._state(token)["status"] == "Error"
                assert bulk._state(token)["completed_blocks"] == 2
                assert frappe.db.count(bulk.DOCTYPE, {"bulk_source_hash": digest}) == 50
                bulk.resume_bulk_import(token)
                frappe.db.commit()
                for _ in range(3):
                    bulk.run_bulk_job(token, "Administrator")
                state = bulk.get_bulk_import_status(token)
                assert state["status"] == "Completado", state
                assert state["created_count"] == 60 and len(state["created"]) == 60
                assert state["options"]["employer_assignments"] == choices
                assert state["completed_blocks"] == 3
                assert frappe.db.count(bulk.DOCTYPE, {"bulk_source_hash": digest}) == 60
                for entry in state["created"]:
                    doc = frappe.get_doc(bulk.DOCTYPE, entry["name"])
                    assert len(doc.rows) == 1
                    assert doc.employer == choices[str(doc.rows[0].source_row)]
                    content = frappe.get_doc("File", {"file_url": doc.source_file}).get_content()
                    assert "CN_EMPRESA_ASIGNADA" in str(content) and doc.employer in str(content)
            assert not bulk._plan(options)["groups"], "A new analysis must skip committed physical rows"
        return {"committed_documents": 60, "blocks": 3, "interrupted_block_rolled_back": True,
                "durable_resume_without_duplicates": True, "manual_employers_preserved": True,
                "original_reads_during_creation": 0, "obsolete_delivery_ignored": True,
                "commit_then_queue_failure_preserves_checkpoint": True, "older_file_api_rollback": True}
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
        for name in companies:
            if frappe.db.exists("CN Employer", name):
                frappe.delete_doc("CN Employer", name, ignore_permissions=True)
        if source and frappe.db.exists("File", source.name):
            frappe.delete_doc("File", source.name, ignore_permissions=True)
        frappe.db.commit()
        cleanup_queue.stop()


def inspect_test_batches():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    return frappe.get_all(store.BATCH, filters={"source_file": ["like", "/private/files/resume-%"]},
                          fields=["name", "source_file", "creation"])


def cleanup_test_batch(token):
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    state = bulk._state(token)
    url = state["options"]["source_file"]
    if not url.startswith("/private/files/resume-"):
        raise RuntimeError("No es un archivo sintético de esta prueba")
    digest = state["options"]["assignments_file_hash"]
    companies = set(state["options"]["employer_assignments"].values())
    if not all(company.startswith("resume-") for company in companies):
        raise RuntimeError("Empresa fuera del alcance de la prueba")
    with patch.object(frappe, "enqueue"):
        for name in frappe.get_all(bulk.DOCTYPE, filters={"bulk_source_hash": digest}, pluck="name"):
            frappe.delete_doc(bulk.DOCTYPE, name, ignore_permissions=True, force=True)
        frappe.db.delete(store.BLOCK, {"batch": token})
        frappe.db.delete(store.BATCH, {"name": token})
        for company in companies:
            frappe.delete_doc("CN Employer", company, ignore_permissions=True)
        for name in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
            frappe.delete_doc("File", name, ignore_permissions=True)
        frappe.db.commit()
    return {"removed_synthetic_batch": token}
