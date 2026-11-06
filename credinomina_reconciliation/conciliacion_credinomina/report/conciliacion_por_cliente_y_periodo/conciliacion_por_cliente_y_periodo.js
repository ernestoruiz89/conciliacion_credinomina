frappe.query_reports["Conciliacion por Cliente y Periodo"] = {
    filters: [
        {fieldname: "period", label: __("Período"), fieldtype: "Link", options: "CN Reconciliation Period", reqd: 1},
        {fieldname: "state", label: __("Estado"), fieldtype: "Select", options: "\nRevisar\nConciliado"},
        {fieldname: "credit_status", label: __("Estado del crédito"), fieldtype: "Data"},
        {fieldname: "only_pending", label: __("Solo recibido pendiente de destino"), fieldtype: "Check", default: 0},
        {fieldname: "search", label: __("Cliente, crédito o comentario"), fieldtype: "Data"},
    ],
    onload(report) {
        report.page.main.find(".cn-client-period-help").remove();
        const help = $('<div class="cn-client-period-help alert alert-info"></div>');
        help.text(__("Banco es el importe identificado en el detalle de depósitos confirmados; Aplicado es la aplicación neta del período. Importe pendiente es el recibido que aún no tiene destino confirmado, después de otros destinos y saldos a favor documentados. Puede diferir de Banco menos Aplicado cuando existen varios depósitos o destinos. Comentario muestra únicamente textos documentados; no sugiere reembolsos. QNA usa el ciclo registrado en el período. El estado del crédito procede del corte de cartera activo y accesible del mes del período o anterior. Los importes no atribuibles se indican como No determinable. Este reporte es de consulta y no modifica conciliaciones."));
        help.prependTo(report.page.main);
    },
    get_datatable_options(options) { return {...options, dynamicRowHeight: true, serialNoColumn: false}; },
    formatter(value, row, column, data, default_formatter) {
        if (["comment", "detail"].includes(column.fieldname)) {
            return value ? `<div style="white-space:pre-wrap;overflow-wrap:anywhere">${frappe.utils.escape_html(String(value))}</div>` : "";
        }
        if (data && ["bank_usd", "applied_usd", "pending_usd"].includes(column.fieldname) && data[column.fieldname] == null) {
            return `<span class="text-muted">${__("No determinable")}</span>`;
        }
        if (column.fieldname === "applied_usd" && Number(value) === 0) return "—";
        const formatted = default_formatter(value, row, column, data);
        if ((data && column.fieldname === "state" && data.state === "Revisar")
            || (column.fieldname === "pending_usd" && Number(value) > 0)) {
            return `<span class="text-danger">${formatted}</span>`;
        }
        return formatted;
    },
};
