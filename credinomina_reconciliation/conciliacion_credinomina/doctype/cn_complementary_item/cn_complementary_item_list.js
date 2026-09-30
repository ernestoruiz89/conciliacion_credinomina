frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["accounting_status", "category", "status"],
    get_indicator(doc) {
        if (doc.category === "Diferencia por tolerancia") {
            return doc.status === "Revertido"
                ? [__("Revertido"), "gray", "status,=,Revertido"]
                : [__("Vigente · tolerancia"), "blue", "category,=,Diferencia por tolerancia|status,=,Vigente"];
        }
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
