frappe.query_reports["Resumen de Conciliacion"] = {
    filters: [
        { fieldname: "view", label: __("Vista"), fieldtype: "Select", options: "Resumen\nDetalle", default: "Resumen", reqd: 1 },
        { fieldname: "period", label: __("Período"), fieldtype: "Link", options: "CN Reconciliation Period" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "only_differences", label: __("Solo con diferencias"), fieldtype: "Check", default: 0 },
        { fieldname: "reconciliation_mode", label: __("Modalidad"), fieldtype: "Select", options: "\nOperativa\nHistorica" },
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena\nFecha exacta" },
        { fieldname: "historical_scope", label: __("Corte histórico"), fieldtype: "Select", options: "\nMensual\nFecha exacta\nRango de fechas" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
        { fieldname: "status", label: __("Estado"), fieldtype: "Select", options: "\nBorrador\nPendiente\nParcial\nCon excedente\nConciliado\nCerrado" },
    ],
    onload(report) {
        report.page.main.find(".cn-reconciliation-help").remove();
        const help = $('<details class="cn-reconciliation-help alert alert-info"></details>');
        $("<summary></summary>").text(__("Cómo leer el reporte de conciliación")).appendTo(help);
        $("<p></p>").text(__("Resumen: una fila por período. Detalle: una fila por período, empresa, cliente y crédito. Importes en US$. Cobranza − aplicación: positivo = falta aplicar; negativo = aplicación en exceso. Aplicación − depósito muestra la diferencia antes de saldos a favor y ajustes. Pendiente = aplicación + ajustes − depósito − saldo a favor utilizado, calculado por aplicación sin compensar faltantes con excedentes. La aplicación es neta de ajustes de aplicación. No aplica indica un período histórico o sin cobranza. El depósito mostrado es únicamente el importe asignado a estas aplicaciones. Solo con diferencias incluye diferencias explicadas por saldos a favor o ajustes; no significa necesariamente deuda pendiente.")).appendTo(help);
        $("<p></p>").text(__("Detalle incluye los comentarios de cobranza y de aplicación del período por crédito, sin repetir textos iguales. El estado del crédito corresponde a la cartera importada, activa y accesible más reciente del mes de la fecha de corte del período o anterior; en períodos históricos se usa el mes del período. Si falta el corte o el crédito, o existe una duplicidad o diferencia de empresa, la columna indica esa situación.")).appendTo(help);
        help.prependTo(report.page.main);
    },
    formatter(value, row, column, data, default_formatter) {
        const fields = ["collection_usd", "application_usd", "collection_difference_usd", "deposit_usd",
            "deposit_difference_usd", "credit_used_usd", "cash_adjustment_usd", "open_usd"];
        if (data && fields.includes(column.fieldname) && data[column.fieldname] == null) {
            const notApplicable = data.collection_control === "No aplica" &&
                ["collection_usd", "collection_difference_usd"].includes(column.fieldname);
            return `<span class="text-muted">${notApplicable ? __("No aplica") : __("No determinable")}</span>`;
        }
        const formatted = default_formatter(value, row, column, data);
        if (data && ["collection_difference_usd", "deposit_difference_usd", "open_usd"].includes(column.fieldname)) {
            const amount = Number(data[column.fieldname]);
            if (Math.abs(amount) >= 0.005) {
                return `<span class="${amount > 0 ? "text-danger" : "text-warning"}">${formatted}</span>`;
            }
        }
        return formatted;
    },
};
