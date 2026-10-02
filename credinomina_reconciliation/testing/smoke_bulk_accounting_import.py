"""Real database/file smoke test; all synthetic records are rolled back."""

import io
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file
from openpyxl import Workbook

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting
from credinomina_reconciliation.parsers import file_sha256, parse_accounting_movements
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import import_source_file


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "bulk-" + frappe.generate_hash(length=8)
    token = None
    commit_patch = patch.object(frappe.db, "commit")
    commit_patch.start()
    try:
        employers = [frappe.get_doc({"doctype": "CN Employer", "employer_name": f"{marker}-{n}",
                     "employer_code": f"{marker}-{n}", "payroll_frequency": "Mensual"}).insert().name
                     for n in range(3)]
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["CUENTA_CONTABLE", "FECHA_APLICA", "NO_CMPTE", "NO_REF", "DESCRIPCION",
                      "DEBITO_DEL_MES", "CREDITO_DEL_MES", "NO_CREDITO", "EMPRESA", "NOMBRE_CLIENTE", "TMOV", "TDOC"])
        for company, employer in enumerate(employers):
            for month in range(4, 7):
                for day in (15, 30):
                    for client in range(2):
                        loan = f"{marker}{company}{client}-1"
                        sheet.append(["123", f"2025-{month:02d}-{day}", f"{marker}-{month}-{day}",
                                      "REF", f"NOTA AL PRESTAMO {loan} PAGO APLICADO", 824.78, 0,
                                      loan, employer, f"Cliente {marker}-{company}-{client}", "12", "05"])
        stream = io.BytesIO()
        workbook.save(stream)
        source = save_file(f"{marker}.xlsx", stream.getvalue(), None, None, is_private=1)
        options = {"source_file": source.file_url, "currency": "NIO", "manual_fx_rate": "36.6243"}
        count_before = frappe.db.count("CN Client")
        plan = bulk._plan(options)
        assert not plan["issues"], plan["issues"]
        assert len(plan["groups"]) == 18
        assert all(group["count"] == 2 and float(group["total_usd"]) == 45.04 for group in plan["groups"])
        assert frappe.db.count("CN Client") == count_before, "Preview must be read-only"

        with patch.object(bulk.frappe, "enqueue"):
            token = bulk.preview_bulk_import(**options)["token"]
        bulk.run_bulk_job(token, "Administrator")
        state = bulk.get_bulk_import_status(token)
        assert state["status"] == "Vista previa", state
        assert state["summary"]["rows"] == 36
        with patch.object(bulk.frappe, "enqueue"):
            bulk.confirm_bulk_import(token)
        # Exercise creation and status delivery but keep all data rollback-only.
        with patch.object(bulk.frappe.db, "commit"):
            with patch.object(bulk.frappe, "enqueue"):
                for _attempt in range(10):
                    bulk.run_bulk_job(token, "Administrator")
                    if bulk._state(token)["status"] == "Completado":
                        break
        state = bulk.get_bulk_import_status(token)
        assert state["status"] == "Completado", state
        created = state["created"]
        assert len(created) == 18
        for result in created:
            document = frappe.get_doc("CN Accounting Import", result["name"])
            assert len(document.rows) == 2
            assert document.total_usd == 45.04
            assert all(str(row.event_date) == result["event_date"] for row in document.rows)
            assert document.bulk_source_hash == plan["file_hash"]
            assert document.bulk_source_file == source.file_url
            assert document.source_file != source.file_url
            individual_file = frappe.get_doc("File", {"file_url": document.source_file})
            assert individual_file.is_private
            # Frappe may add a content suffix to the physical filename.
            assert individual_file.file_name.startswith(document.name) and individual_file.file_name.endswith(".csv")
            individual_content = individual_file.get_content()
            if isinstance(individual_content, str):
                individual_content = individual_content.encode("utf-8")
            assert document.file_hash == file_sha256(individual_content)
            individual_rows = parse_accounting_movements(individual_file.file_name, individual_content)
            assert len(individual_rows) == 2
            assert all(row["amount"] == 824.78 for row in individual_rows)
            assert all(row["employer_text"] == document.employer for row in individual_rows)
            assert frappe.db.exists("File", {"attached_to_doctype": document.doctype,
                                             "attached_to_name": document.name, "file_url": source.file_url})
        reprocessed = frappe.get_doc("CN Accounting Import", created[0]["name"])
        old_row_ids = [row.name for row in reprocessed.rows]
        old_original_rows = [row.source_row for row in reprocessed.rows]
        old_accounting_keys = [row.accounting_source_key for row in reprocessed.rows]
        other_modified = frappe.db.get_value("CN Accounting Import", created[1]["name"], "modified")
        with patch.object(accounting, "_reconcile_sources", return_value={}) as reconcile:
            import_source_file(reprocessed.name)
            reconcile.assert_called_once_with(reprocessed.employer)
            import_source_file(reprocessed.name)
        reprocessed.reload()
        assert [row.name for row in reprocessed.rows] == old_row_ids
        assert [row.source_row for row in reprocessed.rows] == old_original_rows
        assert [row.accounting_source_key for row in reprocessed.rows] == old_accounting_keys
        assert reprocessed.total_usd == 45.04
        assert reprocessed.total_nio == 1649.56
        assert frappe.db.get_value("CN Accounting Import", created[1]["name"], "modified") == other_modified
        for changes in ({"event_date": "2025-07-01"}, {"employer_text": employers[1]}):
            wrong_rows = [{**row.as_dict(), "_csv_original_row": row.source_row, **changes} for row in reprocessed.rows]
            try:
                accounting._validate_bulk_reimport(reprocessed, wrong_rows)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("CSV from another date/company must be rejected")
        repeated = bulk._plan(options)
        assert not repeated["groups"]
        assert len(repeated["already_imported"]) == 36
        # The original all-company report must never be used to reload one group.
        with patch.object(accounting, "_attached_file", return_value=(source, stream.getvalue())):
            try:
                import_source_file(created[0]["name"])
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("A grouped import cannot load the whole file")

        try:
            frappe.set_user("Guest")
            try:
                bulk.get_bulk_import_status(token)
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Guest must not read another user's batch")
        finally:
            frappe.set_user("Administrator")
            frappe.cache.delete_value(bulk._key(token))
        return {"groups": 18, "applications": 36, "months": 3, "companies": 3,
                "duplicate_retry": "OK", "individual_csv_and_reimport": "OK", "stable_row_ids": "OK",
                "private_attachments": "OK", "preview_and_permissions": "OK", "rolled_back": True}
    finally:
        frappe.set_user("Administrator")
        if token:
            frappe.cache.delete_value(bulk._key(token))
        frappe.db.rollback()
        commit_patch.stop()
