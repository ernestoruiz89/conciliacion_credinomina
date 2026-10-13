def execute():
    import frappe
    from credinomina_reconciliation.complementary_subcategories import seed_subcategories

    for doctype in ('cn_complementary_subcategory', 'cn_complementary_item', 'cn_provisional_adjustment'):
        frappe.reload_doc('conciliacion_credinomina', 'doctype', doctype)
    seed_subcategories()
