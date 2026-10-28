"""Classification only: no document save hooks or reconciliation writes."""
import frappe
from frappe import _
from frappe.utils import now_datetime

SCOPES = {
    "CN Remittance Allocation": {"": None, "detail_rows": "CN Remittance Detail"},
    "CN Accounting Import": {"rows": "CN Source Row"},
    "CN Reconciliation Period": {"collection_rows": "CN Collection Row"},
    "CN Complementary Item": {"": None},
}


def target(document, parentfield="", row_name=""):
    scopes = SCOPES.get(document.doctype, {})
    if parentfield not in scopes:
        frappe.throw(_("El origen de la diferencia no es válido."))
    if not parentfield:
        if row_name:
            frappe.throw(_("La fila no corresponde al documento."))
        return document
    row = next((row for row in document.get(parentfield) or [] if row.name == row_name), None)
    if not row or row.doctype != scopes[parentfield]:
        frappe.throw(_("La fila ya no pertenece al documento. Recargue antes de documentar."))
    return row


@frappe.whitelist()
def get_documentation(doctype, name, parentfield="", row_name=""):
    document = frappe.get_doc(doctype, name)
    document.check_permission("read")
    row = target(document, parentfield, row_name)
    return {"modified": str(document.modified), "difference_reason": row.get("difference_reason"),
            "difference_notes": row.get("difference_notes")}


@frappe.whitelist(methods=["POST"])
def document_difference(doctype, name, modified, difference_reason, difference_notes="",
                        parentfield="", row_name=""):
    if doctype not in SCOPES:
        frappe.throw(_("El origen de la diferencia no es válido."))
    # Serialize against reconciliation and ordinary saves of the same parent.
    frappe.db.get_value(doctype, name, "name", for_update=True)
    document = frappe.get_doc(doctype, name)
    document.check_permission("write")
    if document.docstatus == 2:
        frappe.throw(_("No se puede documentar un registro cancelado."))
    if str(document.modified) != str(modified):
        frappe.throw(_("El documento cambió. Recargue antes de documentar la diferencia."))
    row = target(document, parentfield, row_name)
    if not difference_reason or not frappe.db.exists("CN Difference Reason", difference_reason):
        frappe.throw(_("Seleccione una razón de la diferencia válida."))
    frappe.get_doc("CN Difference Reason", difference_reason).check_permission("read")
    notes = str(difference_notes or "").strip()
    if not notes and row.doctype != "CN Remittance Detail":
        frappe.throw(_("Documente el motivo y el soporte de la diferencia."))
    after = {"difference_reason": difference_reason, "difference_notes": notes}
    if _store_documentation(document, row, after):
        _touch_documentation(document)
    return after


def _store_documentation(document, row, after):
    before = {field: row.get(field) or "" for field in ("difference_reason", "difference_notes")}
    if before == after:
        return False
    # Whitelisted fields only; saving the parent would run financial hooks.
    frappe.db.set_value(row.doctype, row.name, after, update_modified=False)
    from frappe.utils import escape_html
    document.add_comment("Comment", _("Diferencia documentada en {0}. Antes: {1}. Después: {2}. Sin modificación de saldos.").format(
        escape_html(row.name), escape_html(frappe.as_json(before)), escape_html(frappe.as_json(after))))
    return True


def _touch_documentation(document):
    modified = now_datetime()
    frappe.db.set_value(document.doctype, document.name,
                        {"modified": modified, "modified_by": frappe.session.user}, update_modified=False)
    frappe.clear_document_cache(document.doctype, document.name)
    return str(modified)
