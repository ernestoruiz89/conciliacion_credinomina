def execute():
    import frappe

    # Existing rows remain client detail. Never infer acceptance from an old
    # difference_reason or create balancing rows automatically.
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_remittance_detail")
    frappe.clear_cache(doctype="CN Remittance Allocation")
