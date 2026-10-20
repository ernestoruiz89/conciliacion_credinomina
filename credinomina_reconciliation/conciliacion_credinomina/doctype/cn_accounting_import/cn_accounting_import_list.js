frappe.listview_settings["CN Accounting Import"] = {
    add_fields: ["employer", "application_amount_usd", "deposit_coverage_usd", "complementary_coverage_usd", "balance_usd", "usd_currency"],
    formatters: {
        application_amount_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        deposit_coverage_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        complementary_coverage_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        balance_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
    },
    onload(listview) {
        if (frappe.model.can_create("CN Accounting Import")) {
            listview.page.add_inner_button(__("Carga masiva"), () => {
                if (typeof frappe.credinomina?.openAccountingBulk !== "function") {
                    frappe.msgprint(__("No se cargó la herramienta de carga masiva. Recargue la página; si persiste, solicite limpiar la caché del sitio después de actualizar la app."));
                    return;
                }
                frappe.credinomina.openAccountingBulk(() => listview.refresh());
            });
        }
        const settings = listview.list_view_settings;
        if (settings.fields) {
            const fields = JSON.parse(settings.fields).filter(field =>
                !["total_usd", "total_application_adjustment_usd", "total_net_applied_usd"].includes(field.fieldname));
            for (const fieldname of ["employer", "application_amount_usd", "deposit_coverage_usd", "complementary_coverage_usd", "balance_usd"]) {
                if (!fields.some(field => field.fieldname === fieldname)) {
                    fields.push({fieldname});
                }
            }
            settings.fields = JSON.stringify(fields);
        }
        const count = listview.meta.fields.filter(field => field.in_list_view).length + 3;
        settings.total_fields = Math.max(Number(settings.total_fields) || 0, count);
        listview.setup_columns();
        listview.render_header(true);
    },
};
