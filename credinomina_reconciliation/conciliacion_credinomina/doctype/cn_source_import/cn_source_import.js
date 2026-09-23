frappe.ui.form.on("CN Source Import", {
    refresh(frm) {
        const legacyDeposits = frm.doc.source_type === "Detalle de depositos";
        const applicationSources = "Movimientos contables (principal)\nTransacciones del core (fallback)";
        frm.set_df_property("source_type", "options",
            legacyDeposits ? `${applicationSources}\nDetalle de depositos` : applicationSources);
        frm.set_df_property("source_type", "read_only", legacyDeposits ? 1 : 0);
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica" },
        }));
        frm.set_df_property("rows", "label", legacyDeposits ? __("Depósitos importados (legado)") : __("Aplicaciones de pago por cliente"));
        if (frm.is_new()) return;

        if (!legacyDeposits) {
            frm.add_custom_button(__("3. Cargar aplicaciones del core"), async () => {
                if (frm.is_dirty()) await frm.save();
                frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.import_source_file",
                    args: { import_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Cargando aplicaciones y actualizando conciliaciones..."),
                }).then(() => frm.reload_doc());
            });
        }

        frm.add_custom_button(__("Actualizar conciliaciones"), () => {
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.reconcile_all_sources",
                freeze: true,
                freeze_message: __("Recalculando conciliaciones..."),
            }).then(() => frm.reload_doc());
        }, __("Más opciones"));
    },
});
