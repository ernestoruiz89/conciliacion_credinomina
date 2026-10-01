frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["accounting_status", "category", "status", "review_status", "accounting_source_key", "docstatus"],
    has_indicator_for_draft: true,
    get_indicator(doc) {
        if (doc.accounting_source_key && doc.docstatus === 0) {
            const status = doc.review_status || "Pendiente de revisión";
            const color = ["No conciliatoria", "Reversión identificada"].includes(status) ? "gray"
                : status === "Lista para conciliar" ? "blue" : "orange";
            return [__(status), color, `review_status,=,${status}`];
        }
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
        listview.page.add_inner_button(__("Movimientos por revisar"), () => {
            listview.filter_area.add([["CN Complementary Item", "review_status", "in", ["Pendiente de identificar", "Pendiente de revisión"]],
                ["CN Complementary Item", "docstatus", "=", 0]]);
        });
        listview.page.add_inner_button(__("Pendientes de registro"), () => {
            listview.filter_area.add([["CN Complementary Item", "accounting_status", "=", "Pendiente de registro"],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        });
    },
};
