frappe.listview_settings["CN Reconciliation Period"] = {
    // Always fetch the indicator, including with personalized list columns.
    // Colors come from DocType states, shared by the list and form header.
    add_fields: ["status", "status_before_close"],
    formatters: {
        status_before_close(value, df, doc) {
            if (doc.status !== "Cerrado") return "—";
            const state = (frappe.get_meta("CN Reconciliation Period").states || [])
                .find(state => state.title === value);
            const color = state ? frappe.scrub(state.color, "-") : "gray";
            return `<span class="indicator-pill ${frappe.utils.escape_html(color)}">${frappe.utils.escape_html(value || __("Sin resultado registrado"))}</span>`;
        },
    },
    onload(listview) {
        const settings = listview.list_view_settings;
        if (settings.fields) {
            const fields = JSON.parse(settings.fields);
            if (!fields.some(field => field.fieldname === "status_before_close")) {
                fields.push({fieldname: "status_before_close"});
            }
            settings.fields = JSON.stringify(fields);
        }
        settings.total_fields = Math.max(Number(settings.total_fields) || 0,
            listview.meta.fields.filter(field => field.in_list_view).length + 3);
        listview.setup_columns();
        listview.render_header(true);
    },
};
