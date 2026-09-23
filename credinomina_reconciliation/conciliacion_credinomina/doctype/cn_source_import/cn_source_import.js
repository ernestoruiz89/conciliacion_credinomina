frappe.ui.form.on("CN Source Import", {
    refresh(frm) {
        frm.set_df_property("source_type", "read_only", 1);
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        frm.set_df_property("rows", "label", __("Aplicaciones de pago por cliente"));
        if (frm.is_new()) return;

        frm.add_custom_button(__("3. Cargar movimientos contables"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.import_source_file",
                args: { import_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando aplicaciones y actualizando conciliaciones..."),
            }).then(() => frm.reload_doc());
        });

        frm.add_custom_button(__("Actualizar conciliaciones"), () => {
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.reconcile_all_sources",
                freeze: true,
                freeze_message: __("Recalculando conciliaciones..."),
            }).then(() => frm.reload_doc());
        }, __("Más opciones"));
    },
});
