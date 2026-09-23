"""Authenticated downloads of import templates for the Frappe forms."""

import frappe
from frappe import _

from credinomina_reconciliation.templates import TEMPLATE_TYPES, build_template_xlsx


@frappe.whitelist(methods=["GET"])
def download_import_template(template_type: str, period_name: str | None = None):
    if template_type not in TEMPLATE_TYPES:
        frappe.throw(_("Tipo de plantilla no admitido."))
    doctype = (
        "CN Remittance Allocation" if template_type == "deposito"
        else "CN Reconciliation Period"
    )
    if not (frappe.has_permission(doctype, "create") or frappe.has_permission(doctype, "read")):
        frappe.throw(_("No tiene permiso para descargar esta plantilla."), frappe.PermissionError)

    collection_rows = ()
    if template_type != "cobranza" and period_name:
        period = frappe.get_doc("CN Reconciliation Period", period_name)
        period.check_permission("read")
        collection_rows = [row.as_dict() for row in period.collection_rows]

    content = build_template_xlsx(template_type, collection_rows)
    frappe.local.response.filename = TEMPLATE_TYPES[template_type][2]
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    frappe.local.response.content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
