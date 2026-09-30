"""Authenticated downloads of import templates for the Frappe forms."""

import re

import frappe
from frappe import _

from credinomina_reconciliation.templates import TEMPLATE_TYPES, build_template_xlsx


@frappe.whitelist(methods=["GET"])
def download_import_template(
    template_type: str, period_name: str | None = None, remittance_name: str | None = None,
):
    if template_type not in TEMPLATE_TYPES:
        frappe.throw(_("Tipo de plantilla no admitido."))
    doctype = (
        "CN Remittance Allocation" if template_type == "deposito"
        else "CN Reconciliation Period"
    )
    if not (frappe.has_permission(doctype, "create") or frappe.has_permission(doctype, "read")):
        frappe.throw(_("No tiene permiso para descargar esta plantilla."), frappe.PermissionError)

    filename = TEMPLATE_TYPES[template_type][2]
    if template_type == "deposito" and remittance_name:
        remittance = frappe.get_doc("CN Remittance Allocation", remittance_name)
        remittance.check_permission("read")
        # Keep the document recognizable without unsafe filename/header characters.
        document_name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", remittance.name).strip(" .")[:150]
        filename = f"{filename[:-5]}_{document_name}.xlsx"

    collection_rows = ()
    if template_type != "cobranza" and period_name:
        period = frappe.get_doc("CN Reconciliation Period", period_name)
        period.check_permission("read")
        collection_rows = [row.as_dict() for row in period.collection_rows]

    content = build_template_xlsx(template_type, collection_rows)
    frappe.local.response.filename = filename
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    frappe.local.response.content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
