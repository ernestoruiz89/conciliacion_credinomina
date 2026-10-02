"""Attach an existing, readable blob without loading or rewriting its contents."""

from types import MethodType

import frappe


def readable_file(url):
    for name in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
        source = frappe.get_doc("File", name)
        if source.has_permission("read"):
            return source
    frappe.throw("No tiene acceso al archivo seleccionado.", frappe.PermissionError)


def _reference_before_insert(attachment):
    # Backport of File.create_attachment_copy's no-blob insert path for older
    # Frappe 15 releases. Document.insert still checks permissions, links,
    # mandatory fields, File.validate and attachment hooks. Never mark new_file:
    # rollback of this reference must not remove the shared original blob.
    for method in ("set_folder_name", "set_is_private", "set_file_name",
                   "validate_attachment_limit", "set_file_type",
                   "validate_file_extension", "validate_private_file_access"):
        callback = getattr(attachment, method, None)
        if callback:
            callback()


def attach_existing_file(source, doctype, name, fieldname):
    if isinstance(source, str):
        source = readable_file(source)
    source.check_permission("read")
    frappe.get_doc(doctype, name).check_permission("write")
    filters = {"file_url": source.file_url, "attached_to_doctype": doctype,
               "attached_to_name": name, "attached_to_field": fieldname}
    existing = frappe.db.exists("File", filters)
    if existing:
        return frappe.get_doc("File", existing)
    copier = getattr(source, "create_attachment_copy", None)
    if callable(copier):
        return copier(doctype, name, fieldname)
    attachment = frappe.get_doc({
        "doctype": "File", **filters,
        **{key: source.get(key) for key in
           ("file_name", "file_size", "file_type", "content_hash", "is_private")},
    })
    attachment.before_insert = MethodType(_reference_before_insert, attachment)
    return attachment.insert()
