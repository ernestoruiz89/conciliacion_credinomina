def execute():
    import frappe

    # Existing decisions retain their behavior. Only an explicit row action
    # enables the manual-only mode; migration never releases cash.
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_remittance_detail")
    frappe.clear_cache(doctype="CN Remittance Allocation")
