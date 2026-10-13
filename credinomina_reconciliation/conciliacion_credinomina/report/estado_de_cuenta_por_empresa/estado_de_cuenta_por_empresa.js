frappe.query_reports["Estado de Cuenta por Empresa"] = {
    filters: [
        {fieldname: "view_mode", label: __("Vista"), fieldtype: "Select", options: "Resumen\nDetalle", default: "Resumen", reqd: 1},
        {fieldname: "compact_view", label: __("Vista compacta"), fieldtype: "Check", default: 1,
            description: __("En Detalle muestra tipo, cliente, saldo, estado y origen. Desmarque para ver la matriz y toda la trazabilidad.")},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "cutoff_date", label: __("Corte histórico al"), fieldtype: "Date",
            description: __("Opcional. Vacío muestra saldos actuales; con fecha excluye operaciones posteriores al corte.")},
        {fieldname: "from_date", label: __("Origen desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Origen hasta"), fieldtype: "Date"},
        {fieldname: "component", label: __("Componente del saldo"), fieldtype: "Select", options: [
            {label: __("Todos"), value: ""},
            ...[["application_pending_usd", "Aplicado sin depósito"], ["core_pending_usd", "Partidas del core pendientes"],
                ["company_credit_usd", "Saldo a favor empresa"], ["client_credit_usd", "Saldo a favor clientes"],
                ["deposit_pending_usd", "Depósitos sin conciliar"], ["company_receivable_usd", "CxC por ajustes"]]
                .map(([value, label]) => ({value, label: __(label)}))]},
        {fieldname: "status", label: __("Estado de conciliación"), fieldtype: "Autocomplete", options: ["Conciliado", "Pendiente", "Parcial", "Revisar distribución", "Sin conversión US$", "Importado del core; sin confirmar", "Pendiente de cobro", "Parcialmente cobrada", "Revisar cobros"],
            description: __("Las sugerencias se actualizan con los estados de las partidas de esta consulta.")},
        {fieldname: "category", label: __("Concepto"), fieldtype: "Autocomplete", options: ["Por clasificar", "Ajuste de conciliación", "Ajuste de aplicación", "Compensación entre partidas", "Otros ingresos", "Cobranza administrativa", "Saldo a favor del cliente", "Saldo a favor de la empresa"]},
        {fieldname: "reason_type", label: __("Motivo"), fieldtype: "Autocomplete", options: ["Error de la empresa", "Pago adicional no informado", "Por refinanciamiento", "Por cancelación", "Otro por aclarar"]},
        {fieldname: "subcategory", label: __("Subcategoría"), fieldtype: "Link", options: "CN Complementary Subcategory"},
        {fieldname: "accounting_status", label: __("Registro contable"), fieldtype: "Select", options: "\nPendiente de registro\nAsiento informado\nImportada del core\nRegistrada\nNo requiere registro"},
        {fieldname: "management_status", label: __("Gestión externa"), fieldtype: "Autocomplete", options: ["Pendiente", "Parcialmente resuelto", "Resuelto", "Pendiente de cobro", "Parcialmente cobrada", "Revisar cobros"]},
        {fieldname: "treatment_status", label: __("Tratamiento"), fieldtype: "Autocomplete", options: ["Documentado", "Vigente", "Registro contable verificado", "Por aclarar (no conciliatoria)", "Pendiente de confirmar", "Conciliada", "Parcial", "Pendiente", "Revisar distribución"]},
        {fieldname: "search", label: __("Cliente, crédito o documento"), fieldtype: "Data"},
    ],
    onload(report) {
        report.page.wrapper.addClass?.("cn-company-statement");
        frappe.dom?.set_style?.(`.cn-company-statement .dt-row-header {height:42px !important;}
        .cn-company-statement .dt-row-header .dt-cell__content {
            white-space:normal;line-height:1.2;overflow:visible;padding:4px 8px;
        }`, "cn-company-statement-style");
        const advanced = ["cutoff_date", "category", "reason_type", "subcategory", "accounting_status", "management_status", "treatment_status", "search"];
        let expanded = false;
        const display = () => advanced.forEach(name => {
            const control = report.get_filter(name);
            control.$wrapper.toggle(expanded || Boolean(control.get_value()));
        });
        const button = report.page.add_inner_button(__("Más filtros"), () => {
            expanded = !expanded;
            button.text(__(expanded ? "Menos filtros" : "Más filtros"));
            display();
        });
        display(); // Active filters are never hidden, including route-supplied cuts.
        report.page.wrapper.off("click.cn_statement_detail", ".cn-statement-detail");
        report.page.wrapper.on("click.cn_statement_detail", ".cn-statement-detail", function(event) {
            event.preventDefault();
            const detail = JSON.parse($(this).attr("data-detail"));
            if (report.get_filter_value("view_mode") !== "Detalle") {
                report.cn_summary_filters = Object.fromEntries(
                    frappe.query_reports["Estado de Cuenta por Empresa"].filters.map(filter =>
                        [filter.fieldname, report.get_filter_value(filter.fieldname) ?? ""]));
            }
            report.set_filter_value({view_mode: "Detalle", employer: detail.employer || "", component: detail.component || ""});
        });
        report.page.add_inner_button(__("Volver al resumen"), () => {
            const filters = report.cn_summary_filters || {view_mode: "Resumen", component: ""};
            report.cn_summary_filters = null;
            return report.set_filter_value(filters);
        });
    },
    get_datatable_options(options) { return {...options, cellHeight: 42}; },
    after_datatable_render() {
        const report = frappe.query_report;
        const states = report.data?.find(row => Array.isArray(row._available_statuses))?._available_statuses;
        if (states) report.get_filter("status").set_data([...new Set([...states, report.get_filter_value("status")].filter(Boolean))]);
    },
    formatter(value, row, column, data, default_formatter) {
        if (data && column.fieldtype === "Currency" && data[column.fieldname] == null) {
            return `<span class="text-muted" title="${__("Importe o signo sin determinar; revise el detalle.")}">—</span>`;
        }
        const html = default_formatter(value, row, column, data);
        if (data && !data.source_document && data.employer &&
                ((column.fieldtype === "Currency" && Number(value)) || column.fieldname === "pending_count")) {
            const args = frappe.utils.escape_html(JSON.stringify({employer: data.employer,
                component: column.fieldname === "balance_usd" || column.fieldname === "pending_count" ? "" : column.fieldname}));
            return `<button type="button" class="cn-statement-detail" data-detail="${args}" title="${__("Ver partidas pendientes")}" style="border:0;background:transparent;color:var(--text-color);font:inherit;text-decoration:underline;cursor:pointer">${html}</button>`;
        }
        return html;
    },
};
