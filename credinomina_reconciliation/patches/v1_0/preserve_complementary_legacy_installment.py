"""Preserve previous matching evidence after removing the editable field."""


def execute():
    import frappe

    doctype = "CN Complementary Item"
    if not frappe.db.has_column(doctype, "installment_number"):
        return  # New sites never had the old field.
    if not frappe.db.has_column(doctype, "legacy_installment_number"):
        frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_complementary_item")
    frappe.db.sql("""UPDATE `tabCN Complementary Item`
        SET legacy_installment_number = installment_number
        WHERE COALESCE(legacy_installment_number, '') = ''
          AND COALESCE(installment_number, '') != ''""")
