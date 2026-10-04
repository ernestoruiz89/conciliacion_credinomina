frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["accounting_status", "category", "status", "review_status", "accounting_source_key", "docstatus", "compensation_status", "credit_management_status", "credit_pending_usd", "result"],
    has_indicator_for_draft: true,
    get_indicator(doc) {
        if (["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(doc.category)) {
            const status = doc.docstatus === 2 ? "Cancelado" : doc.docstatus === 0 ? "Por confirmar" : doc.credit_management_status || "Pendiente";
            return [__(`${doc.category} · ${status}`), doc.docstatus === 2 ? "gray" : status === "Resuelto" ? "green" : "orange", `category,=,${doc.category}`];
        }
        if (doc.category === "Compensación entre partidas") {
            const status = doc.compensation_status || "Sin compensar";
            return [__(status), status === "Compensada totalmente" ? "green" : "orange", `compensation_status,=,${status}`];
        }
        if (doc.category === "Ajuste de aplicación") {
            const status = doc.docstatus === 2 ? "Ajuste cancelado" : doc.review_status || "Ajuste pendiente de completar";
            return [__(status), doc.docstatus === 1 ? "blue" : doc.docstatus === 2 ? "gray" : "orange", `review_status,=,${status}`];
        }
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
        const accounting = doc.accounting_status || "Pendiente de registro";
        const label = accounting === "Registrada" ? "Registro verificado · ver conciliación" : accounting;
        return [__(label), ["Registrada", "Importada del core", "No requiere registro"].includes(accounting) ? "blue" : "orange", `accounting_status,=,${accounting}`];
    },
    onload(listview) {
        listview.page.add_inner_button(__("Saldos de empresas pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Saldo a favor de la empresa"],
                ["CN Complementary Item", "credit_pending_usd", ">", 0], ["CN Complementary Item", "docstatus", "=", 1]]);
        });
        listview.page.add_inner_button(__("Saldos de clientes pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Saldo a favor del cliente"],
                ["CN Complementary Item", "credit_pending_usd", ">", 0], ["CN Complementary Item", "docstatus", "=", 1]]);
        });
        listview.page.add_inner_button(__("Compensaciones pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Compensación entre partidas"],
                ["CN Complementary Item", "compensation_status", "in", ["Sin compensar", "Compensada parcialmente"]],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        });
        listview.page.add_inner_button(__("Movimientos por revisar"), () => {
            listview.filter_area.add([["CN Complementary Item", "review_status", "in", ["Pendiente de identificar", "Pendiente de revisión", "Ajuste pendiente de completar", "Ajuste pendiente de confirmar", "Sin compensar", "Compensada parcialmente"]],
                ["CN Complementary Item", "docstatus", "=", 0]]);
        });
        listview.page.add_inner_button(__("Pendientes de registro"), () => {
            listview.filter_area.add([["CN Complementary Item", "accounting_status", "in", ["Pendiente de registro", "Asiento informado"]],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        });
    },
};
