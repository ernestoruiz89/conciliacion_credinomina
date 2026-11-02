"""Remove superseded portfolio attachments after their replacement is saved."""
import frappe

from credinomina_reconciliation.import_file_guard import IMPORTED_FILES

DOCTYPE = "CN Credit Portfolio Snapshot"


def prune_previous_files(snapshot_name, source_file):
    """Called only after a permission-checked header save under its row lock."""
    current_file = frappe.db.exists("File", {"file_url": source_file,
        "attached_to_doctype": DOCTYPE, "attached_to_name": snapshot_name})
    if not current_file:
        return []
    replacement = frappe.get_doc("File", current_file)
    if replacement.is_remote_file or not replacement.exists_on_disk():
        return []
    previous_urls = set()
    for version in frappe.get_all("Version", filters={"ref_doctype": DOCTYPE,
            "docname": snapshot_name}, fields=["data"], limit_page_length=0):
        for change in (frappe.parse_json(version.data) or {}).get("changed", []):
            if len(change) >= 3 and change[0] == "source_file" and change[1]:
                previous_urls.add(change[1])
    previous_urls.discard(source_file)
    if not previous_urls:
        return []
    # Only files that were previously saved as this cut's source are obsolete.
    # A different upload still in progress and unrelated supports stay attached.
    attachments = frappe.get_all("File", filters={"attached_to_doctype": DOCTYPE,
        "attached_to_name": snapshot_name, "file_url": ["in", sorted(previous_urls)]},
        fields=["name", "file_url", "attached_to_field"], limit_page_length=0)
    removed = []
    for attachment in attachments:
        current = frappe.db.get_value("File", {"name": attachment.name,
            "attached_to_doctype": DOCTYPE, "attached_to_name": snapshot_name,
            "file_url": attachment.file_url}, ["name", "attached_to_field"], as_dict=True, for_update=True)
        if not current or current.attached_to_field not in (None, "", "source_file"):
            continue
        frappe.db.set_value("File", attachment.name, {
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        }, update_modified=False)
        frappe.clear_document_cache("File", attachment.name)
        frappe.enqueue("credinomina_reconciliation.portfolio_files._delete_detached_file",
            name=attachment.name, url=attachment.file_url, queue="short", enqueue_after_commit=True)
        removed.append(attachment.name)
    return removed


def _delete_detached_file(name, url):
    """Physical deletion must not survive a rollback of the replacement."""
    attachment = frappe.db.get_value("File", name,
        ["file_url", "attached_to_doctype", "attached_to_name"], as_dict=True, for_update=True)
    if not attachment or attachment.file_url != url or attachment.attached_to_doctype or attachment.attached_to_name:
        return
    # A shared import may still need this File record to read its blob.
    if any(frappe.db.exists(doctype, {field: url, provenance: ["!=", ""]})
           for doctype, field, provenance in IMPORTED_FILES):
        return
    # Parent write permission was checked before detaching this exact file.
    # All standard File deletion hooks still run, including shared-blob checks.
    frappe.delete_doc("File", name, ignore_permissions=True)
