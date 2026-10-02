"""Authenticated downloads of import templates for the Frappe forms."""

import re

import frappe
from frappe import _

from credinomina_reconciliation.templates import TEMPLATE_TYPES, build_template_xlsx


@frappe.whitelist(methods=["GET"])
def download_import_template(
    template_type: str, period_name: str | None = None, remittance_name: str | None = None,
    period_names=None,
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

    names = frappe.parse_json(period_names) if isinstance(period_names, str) else period_names
    if names is not None and (not isinstance(names, list) or any(not isinstance(name, str) for name in names)):
        frappe.throw(_("La selección de períodos no es válida."))
    names = list(dict.fromkeys(names or ([period_name] if period_name else [])))
    collection_rows = []
    if template_type != "cobranza":
        for name in names:
            period = frappe.get_doc("CN Reconciliation Period", name)
            period.check_permission("read")
            collection_rows.extend({**row.as_dict(), "employer": period.employer} for row in period.collection_rows)

    content = build_template_xlsx(template_type, collection_rows)
    frappe.local.response.filename = filename
    frappe.local.response.filecontent = content
    frappe.local.response.type = "download"
    frappe.local.response.content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
