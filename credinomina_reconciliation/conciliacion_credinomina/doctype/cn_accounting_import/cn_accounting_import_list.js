frappe.listview_settings["CN Accounting Import"] = {
    add_fields: ["employer", "total_usd", "usd_currency"],
    onload(listview) {
        const settings = listview.list_view_settings;
        if (settings.fields) {
            const fields = JSON.parse(settings.fields);
            for (const fieldname of ["employer", "total_usd"]) {
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
