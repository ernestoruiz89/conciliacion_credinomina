"""An uploaded 3 MB replacement saves only the header; rollback all fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as portfolio
from credinomina_reconciliation.import_file_guard import guard_import_file_deletion
from credinomina_reconciliation.portfolio_files import _delete_detached_file
from credinomina_reconciliation.file_references import attach_existing_file


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "HEADER-UPLOAD-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            cut = frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot",
                source_file=f"/private/files/{marker}.xlsx", report_date="2097-03-31",
                status="Importado", file_hash="ORIGINAL-HASH", disabled=1,
                rows=[dict(credit_number=f"{marker}-{i}-1", credit_status="Corriente")
                      for i in range(1000)])).insert()

            def upload(label, content):
                return frappe.get_doc(dict(doctype="File", file_name=f"{marker}-{label}.xlsx",
                    attached_to_doctype=cut.doctype, attached_to_name=cut.name,
                    attached_to_field="source_file", content=content, is_private=1)).insert()

            original_content = b"original-" + marker.encode()
            original = upload("original", original_content)
            cut.source_file = original.file_url
            cut.save()
            rows = frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
            before = frappe.db.get_value(cut.doctype, cut.name,
                ["file_hash", "row_count", "status", "report_date", "imported_on", "imported_by"], as_dict=True)
            # The existing deletion protection remains in force for this file.
            with patch("credinomina_reconciliation.import_file_guard.has_core_removal_role", return_value=False):
                try:
                    guard_import_file_deletion(original)
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError("Imported evidence lost its deletion protection")

            content = marker.encode().ljust(3111765, b"x")
            replacement = upload("replacement", content)
            # A replacement not yet saved must stay attached even if another
            # saved replacement is cleaned up first.
            pending = upload("pending", b"pending-" + marker.encode())
            with patch.object(type(replacement), "exists_on_disk", return_value=False):
                try:
                    portfolio.save_portfolio_header(cut.name, replacement.file_url, "", 1, str(cut.modified))
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("An unavailable upload replaced the previous file")
            assert frappe.db.get_value(cut.doctype, cut.name, "source_file") == original.file_url
            assert frappe.db.get_value("File", original.name, "attached_to_name") == cut.name
            assert original.exists_on_disk()
            frappe.db.savepoint("portfolio_file_replacement")
            with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
                result = portfolio.save_portfolio_header(cut.name, replacement.file_url,
                    "Archivo corregido pendiente de importar", 1, str(cut.modified))
            assert not any("tabCN Credit Portfolio Row" in str(call.args[0]) for call in sql.call_args_list)
            assert set(result) == {"source_file", "notes", "disabled", "modified", "modified_by", "removed_attachments"}
            assert result["removed_attachments"] == [original.name]
            assert len(frappe.as_json(result)) < 512
            assert result["source_file"] == replacement.file_url
            assert before == frappe.db.get_value(cut.doctype, cut.name, list(before), as_dict=True)
            assert rows == frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
            assert frappe.db.exists("File", original.name) and frappe.db.exists("File", replacement.name)
            assert not frappe.db.get_value("File", original.name, "attached_to_name")
            assert frappe.db.get_value("File", replacement.name, "attached_to_name") == cut.name
            assert frappe.db.get_value("File", pending.name, "attached_to_name") == cut.name
            for attachment, expected in ((original, original_content), (replacement, content)):
                stored = frappe.get_doc("File", attachment.name).get_content()
                assert (stored.encode() if isinstance(stored, str) else stored) == expected
            assert replacement.file_size == 3111765
            version = frappe.get_last_doc("Version", filters={"ref_doctype": cut.doctype, "docname": cut.name})
            changed = frappe.parse_json(version.data)["changed"]
            assert ["source_file", original.file_url, replacement.file_url] in changed
            try:
                portfolio.save_portfolio_header(cut.name, replacement.file_url, "", 1, str(cut.modified))
            except frappe.TimestampMismatchError:
                pass
            else:
                raise AssertionError("A stale header update was accepted")
            # Rolling back the header also restores the previous attachment;
            # a delayed cleanup job must then leave its contents intact.
            frappe.db.rollback(save_point="portfolio_file_replacement")
            _delete_detached_file(original.name, original.file_url)
            assert frappe.db.get_value("File", original.name, "attached_to_name") == cut.name
            assert frappe.db.get_value(cut.doctype, cut.name, "source_file") == original.file_url
            portfolio.save_portfolio_header(cut.name, replacement.file_url, "", 1, str(cut.modified))
            _delete_detached_file(original.name, original.file_url)  # Simulate the after-commit worker.
            assert not frappe.db.exists("File", original.name)
            assert not original.exists_on_disk()
            assert replacement.exists_on_disk()
            assert rows == frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
            other = frappe.get_doc(dict(doctype=cut.doctype, source_file=replacement.file_url,
                file_hash="SHARED-IMPORT")).insert()
            shared = attach_existing_file(replacement, other.doctype, other.name, "source_file")
            cut.reload()
            result = portfolio.save_portfolio_header(cut.name, pending.file_url, "", 1, str(cut.modified))
            assert result["removed_attachments"] == [replacement.name]
            _delete_detached_file(replacement.name, replacement.file_url)
            assert not frappe.db.get_value("File", replacement.name, "attached_to_name")
            assert frappe.db.get_value("File", shared.name, "attached_to_name") == other.name
            assert shared.exists_on_disk() and pending.exists_on_disk()
            assert frappe.db.get_value(other.doctype, other.name, "source_file") == replacement.file_url
            # An ordinary Save also cleans files accumulated before this fix.
            frappe.db.set_value("File", replacement.name, dict(attached_to_doctype=cut.doctype,
                attached_to_name=cut.name, attached_to_field="source_file"), update_modified=False)
            cut.reload()
            cut.notes = "Limpiar adjuntos anteriores"
            cut.save()
            assert not frappe.db.get_value("File", replacement.name, "attached_to_name")
            assert shared.exists_on_disk() and pending.exists_on_disk()
            assert rows == frappe.get_all("CN Credit Portfolio Row", filters={"parent": cut.name}, fields=["*"], order_by="idx")
            return dict(upload_bytes=replacement.file_size, preserved_rows=len(rows), header_only=True,
                source_change_audited=True, previous_attachment_removed=True,
                failed_save_preserves_old_file=True, pending_upload_preserved=True,
                unavailable_upload_preserves_previous_file=True,
                shared_import_file_preserved=True,
                ordinary_save_cleans_previous_attachments=True,
                import_results_unchanged=True, stale_update_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
