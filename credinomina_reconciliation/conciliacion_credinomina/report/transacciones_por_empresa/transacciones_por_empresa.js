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
        if (column.fieldname === "employer" && data && !value) return __("Sin empresa identificada");
        if (!/^m(0[1-9]|1[0-2])$/.test(column.fieldname) || !data) {
            return default_formatter(value, row, column, data);
        }
        if (!Number(value)) return '<span class="text-muted" title="' + __("Sin transacciones") + '">—</span>';
        const key = column.fieldname;
        const state = data[key + "_state"] || "Pendiente";
        const colors = {Conciliado: ["#dcfce7", "#14532d"], Parcial: ["#ffedd5", "#9a3412"], Pendiente: ["#fee2e2", "#991b1b"]};
        const [background, foreground] = colors[state] || colors.Pendiente;
        const count = suffix => Math.max(0, Number(data[key + suffix]) || 0);
        const description = `${__(state)} · ${__("Conciliadas")}: ${count("_conciliado")} · ${__("Parciales")}: ${count("_parcial")} · ${__("Pendientes")}: ${count("_pendiente")}`;
        const escaped = frappe.utils.escape_html(description);
        return `<span style="display:block;text-align:center;background:${background};color:${foreground};font-weight:600;padding:5px 6px;border-radius:4px" title="${escaped}" aria-label="${escaped}">${default_formatter(value, row, column, data)}</span>`;
    },
};
