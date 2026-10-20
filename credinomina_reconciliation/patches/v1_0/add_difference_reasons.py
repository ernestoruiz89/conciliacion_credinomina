def execute():
    import frappe

    from credinomina_reconciliation.difference_reasons import seed_reasons
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace

    for doctype in ("cn_difference_reason", "cn_remittance_detail", "cn_source_row",
                    "cn_remittance_allocation", "cn_accounting_import"):
        frappe.reload_doc("conciliacion_credinomina", "doctype", doctype)
    seed_reasons()
    refresh_workspace()
