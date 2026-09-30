frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["accounting_status"],
    get_indicator(doc) {
        return doc.accounting_status === "Registrada"
            ? [__("Registrada"), "green", "accounting_status,=,Registrada"]
            : [__("Pendiente de registro"), "orange", "accounting_status,=,Pendiente de registro"];
    },
    onload(listview) {
        listview.page.add_inner_button(__("Pendientes de registro"), () => {
            listview.filter_area.add([["CN Complementary Item", "accounting_status", "=", "Pendiente de registro"],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        });
    },
};
