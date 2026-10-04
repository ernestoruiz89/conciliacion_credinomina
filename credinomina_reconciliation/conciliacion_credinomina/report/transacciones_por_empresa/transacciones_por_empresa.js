frappe.query_reports["Transacciones por Empresa"] = {
    filters: [
        {fieldname: "year", label: __("Año"), fieldtype: "Int", reqd: 1,
            default: Number(frappe.datetime.get_today().slice(0, 4))},
        {fieldname: "transaction_type", label: __("Tipo de transacción"), fieldtype: "Select", reqd: 1,
            options: "Aplicaciones\nDepósitos", default: "Aplicaciones"},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "include_drafts", label: __("Incluir borradores"), fieldtype: "Check", default: 0},
    ],
    formatter(value, row, column, data, default_formatter) {
        const key = column?.fieldname || "";
        const html = default_formatter(value, row, column, data);
        if (key === "employer" && data && !value) return __("Sin empresa identificada");
        if (key === "total" && data) return `<div style="font-weight:bold;text-align:right">${html}</div>`;
        if (!/^m(0[1-9]|1[0-2])$/.test(key) || !data) {
            return html;
        }
        if (!Number(value)) return '<span class="text-muted" title="' + __("Sin transacciones") + '">—</span>';
        const state = data[key + "_state"] || "Pendiente";
        const colors = {Conciliado: ["#dcfce7", "#14532d"], Parcial: ["#ffedd5", "#9a3412"], Pendiente: ["#fee2e2", "#991b1b"]};
        const [background, foreground] = colors[state] || colors.Pendiente;
        const count = suffix => Math.max(0, Number(data[key + suffix]) || 0);
        const description = `${__(state)} · ${__("Conciliadas")}: ${count("_conciliado")} · ${__("Parciales")}: ${count("_parcial")} · ${__("Pendientes")}: ${count("_pendiente")}`;
        const escaped = frappe.utils.escape_html(description);
        // Offset DataTable's content padding so the status fills the cell.
        // Color encodes reconciliation status, never transaction volume.
        return `<div style="background-color:${background};color:${foreground};width:calc(100% + 8px);margin:-4px;padding:4px;box-sizing:border-box;text-align:right;font-weight:600;border-radius:3px" title="${escaped}" aria-label="${escaped}">${html}</div>`;
    },
};
