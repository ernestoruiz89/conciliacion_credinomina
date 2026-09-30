function update_currency_fields(frm) {
    const is_nio = frm.doc.currency === "NIO";
    frm.toggle_reqd("currency", frm.is_new() || frm.doc.status === "Borrador");
    frm.toggle_display("manual_fx_rate", is_nio);
    frm.toggle_reqd("manual_fx_rate", is_nio);
}

frappe.ui.form.on("CN Source Import", {
    refresh(frm) {
        update_currency_fields(frm);
        frm.set_df_property("source_type", "read_only", 1);
        frm.set_query("employer", () => ({filters: {active: 1}}));
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("portfolio_snapshot", () => ({
            query: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.get_company_portfolio_snapshots",
            filters: {employer: frm.doc.employer},
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

    currency(frm) {
        if (frm.doc.currency !== "NIO") {
            frm.set_value("manual_fx_rate", 0);
        }
        update_currency_fields(frm);
    },

    employer(frm) {
        frm.set_value("portfolio_snapshot", "");
        frm.set_value("historical_period", "");
    },
});
