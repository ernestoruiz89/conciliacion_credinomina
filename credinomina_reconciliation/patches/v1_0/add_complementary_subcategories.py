def execute():
    import frappe
    from credinomina_reconciliation.complementary_subcategories import seed_subcategories, legacy_subcategory
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace

    for doctype in ('cn_complementary_subcategory', 'cn_complementary_item'):
        frappe.reload_doc('conciliacion_credinomina', 'doctype', doctype)
    seed_subcategories()
    for item in frappe.get_all('CN Complementary Item', filters={
        'category': 'Ajuste de conciliación', 'subcategory': ['is', 'not set'],
    }, fields=['name', 'description', 'amount_usd', 'accounting_source_key'], limit_page_length=0):
        name, effect = legacy_subcategory(item)
        frappe.db.set_value('CN Complementary Item', item.name,
            {'subcategory': name, 'subcategory_effect': effect}, update_modified=False)
        frappe.clear_document_cache('CN Complementary Item', item.name)
    refresh_workspace()
