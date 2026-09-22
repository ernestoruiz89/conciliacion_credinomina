frappe.ui.form.on("CN Source Import", {
    refresh(frm) {
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        if (frm.is_new()) return;

        frm.add_custom_button(__("Importar y conciliar"), () => {
            frappe.call({
                method: "credinomina_reconciliation.credinomina_reconciliation.doctype.cn_source_import.cn_source_import.import_source_file",
                args: { import_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Importando y conciliando..."),
            }).then(() => frm.reload_doc());
        });

        frm.add_custom_button(__("Reconciliar todas las fuentes"), () => {
            frappe.call({
                method: "credinomina_reconciliation.credinomina_reconciliation.doctype.cn_source_import.cn_source_import.reconcile_all_sources",
                freeze: true,
                freeze_message: __("Recalculando conciliaciones..."),
            }).then(() => frm.reload_doc());
        });
    },
});
