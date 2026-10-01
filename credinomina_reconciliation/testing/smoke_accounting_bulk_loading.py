"""Check the real metadata delivered to list-only and form-only sessions."""
import frappe
from frappe.desk.form.meta import FormMeta


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    meta = FormMeta("CN Accounting Import", cached=False)
    for field in ("__js", "__list_js"):
        script = meta.get(field) or ""
        assert "frappe.credinomina.openAccountingBulk = function" in script, field
        assert 'frappe.require("/assets/credinomina_reconciliation/js/accounting_bulk.js"' not in script, field
        assert "credinomina is not defined" not in script
    return {"form_embeds_dialog": "OK", "list_embeds_dialog": "OK", "public_asset_not_required": "OK"}
