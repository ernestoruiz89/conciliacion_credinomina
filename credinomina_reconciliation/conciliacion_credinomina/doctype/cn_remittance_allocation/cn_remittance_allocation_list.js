frappe.listview_settings["CN Remittance Allocation"] = {
    // Currency is required to format the original deposit, not its USD equivalent.
    add_fields: ["deposit_date", "deposit_amount", "deposit_currency", "bank_account"],
    onload(listview) {
        const settings = listview.list_view_settings;
        if (settings.fields) {
            const fields = JSON.parse(settings.fields);
            for (const fieldname of ["deposit_date", "deposit_amount", "bank_account"]) {
                if (!fields.some(field => field.fieldname === fieldname)) {
                    fields.push({fieldname});
                }
            }
            settings.fields = JSON.stringify(fields);
        }
        // Frappe 15 otherwise truncates the default list on smaller screens.
        // Keep the existing columns, including company and reconciliation result.
        const count = listview.meta.fields.filter(field => field.in_list_view).length + 3;
        settings.total_fields = Math.max(Number(settings.total_fields) || 0, count);
        listview.setup_columns();
        listview.render_header(true);
    },
};
