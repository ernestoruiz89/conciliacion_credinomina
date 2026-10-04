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
        const colors = {Conciliado: ["#dcfce7", "#14532d"], Parcial: ["#fb923c", "#431407"], Pendiente: ["#fee2e2", "#991b1b"]};
        const percentage = data[key + "_percentage"];
        const known = percentage != null && Number.isFinite(Number(percentage));
        const [background, foreground] = state === "Parcial" && known && data[key + "_half_covered"]
            ? ["#fef9c3", "#713f12"] : colors[state] || colors.Pendiente;
        const count = suffix => Math.max(0, Number(data[key + suffix]) || 0);
        const formatAmount = amount => Number(amount).toLocaleString("es-NI", {minimumFractionDigits: 2, maximumFractionDigits: 2});
        const percentageLabel = known && Number(percentage) < 50 && Number(percentage).toFixed(2) === "50.00"
            ? "<50" : formatAmount(percentage);
        const progress = known
            ? `${__("Importe conciliado")}: ${percentageLabel}% · US$ ${formatAmount(data[key + "_covered_usd"])} / ${formatAmount(data[key + "_total_usd"])}`
            : __("Porcentaje del importe no disponible");
        const description = `${__(state)} · ${progress} · ${__("Conciliadas")}: ${count("_conciliado")} · ${__("Parciales")}: ${count("_parcial")} · ${__("Pendientes")}: ${count("_pendiente")}`;
        const escaped = frappe.utils.escape_html(description);
        // Offset DataTable's content padding so the status fills the cell.
        // Partial-state color uses the amount covered, never transaction count.
        return `<div style="background-color:${background};color:${foreground};width:calc(100% + 8px);margin:-4px;padding:4px;box-sizing:border-box;text-align:right;font-weight:600;border-radius:3px" title="${escaped}" aria-label="${escaped}">${html}</div>`;
    },
};
