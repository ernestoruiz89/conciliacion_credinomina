frappe.listview_settings["CN Remittance Allocation"] = {
    // Currency is required to format the original deposit, not its USD equivalent.
    add_fields: ["deposit_date", "deposit_amount", "deposit_currency", "bank_account", "result", "financial_status",
        "docstatus", "amount_usd", "allocated_usd", "credit_coverage_usd", "complementary_coverage_usd",
        "justified_surplus_usd", "unclassified_usd", "detail_status", "usd_currency"],
    formatters: {
        credit_coverage_usd(value, df, doc) {
            return doc.docstatus !== 1 || value == null ? "—" : frappe.format(value, df, null, doc);
        },
        complementary_coverage_usd(value, df, doc) {
            return doc.docstatus !== 1 || value == null ? "—" : frappe.format(value, df, null, doc);
        },
        unclassified_usd(value, df, doc) {
            if (doc.docstatus !== 1 || doc.amount_usd == null) return "—";
            const cents = amount => Math.round(Number(amount || 0) * 100);
            return frappe.format((cents(doc.amount_usd) - cents(doc.allocated_usd)
                - cents(doc.justified_surplus_usd)) / 100, df, null, doc);
        },
    },
    has_indicator_for_draft: true,
    get_indicator(doc) {
        if (doc.financial_status) {
            const state = doc.financial_status;
            const green = ["Conciliado", "Conciliado con saldo a favor"].includes(state);
            const review = /revisar|Revisar|Parcial|Detalle pendiente/.test(state);
            return [__(state), green ? "green" : state === "No participa" ? "gray" : review ? "orange" : "blue",
                `financial_status,=,${state}`];
        }
        if (doc.docstatus === 2) return [__("No participa"), "gray", "docstatus,=,2"];
        if (doc.docstatus !== 1) return [__("Sin conciliar"), "blue", "docstatus,=,0"];
        const result = doc.result || "Pendiente";
        const cents = value => Math.round(Number(value || 0) * 100);
        const credit = cents(doc.justified_surplus_usd);
        const pending = cents(doc.amount_usd) - cents(doc.allocated_usd) - credit;
        const recordedSettlement = ["Conciliado", "Conciliado con saldo a favor", "Conciliado con saldo a favor del cliente"].includes(result)
            || credit > 0 && ["Parcial con saldo a favor", "Saldo a favor documentado"].includes(result);
        const review = (doc.detail_status || "").startsWith("Revisar")
            || ["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(result)
            || recordedSettlement && (!Number.isFinite(pending) || pending !== 0 || credit < 0
                || cents(doc.allocated_usd) < 0 || cents(doc.unclassified_usd) !== 0
                || doc.amount_usd != null && Number(doc.amount_usd) <= 0);
        const settled = recordedSettlement && !review;
        const label = review && recordedSettlement ? "Por revisar" : settled && credit > 0 ? "Conciliado con saldo a favor" : result;
        return [__(label), settled ? "green" : review ? "orange" : "blue", `financial_status,=,${label}`];
    },
    onload(listview) {
        const settings = listview.list_view_settings;
        if (settings.fields) {
            const fields = JSON.parse(settings.fields).filter(field => field.fieldname !== "result");
            for (const fieldname of ["deposit_date", "deposit_amount", "bank_account", "financial_status",
                "amount_usd", "credit_coverage_usd", "complementary_coverage_usd", "unclassified_usd"]) {
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
