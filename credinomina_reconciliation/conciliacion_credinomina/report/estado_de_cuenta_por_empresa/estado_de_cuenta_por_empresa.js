frappe.query_reports["Estado de Cuenta por Empresa"] = {
    bridge_reset() {
        return {statement_scope:"Todos", from_date:"", to_date:"", source_account:"", source_currency:"",
            component:"", accounting_queue:0, status:"", origin_type:"", category:"", reason_type:"", subcategory:"",
            accounting_status:"", management_status:"", treatment_status:"", search:""};
    },
    filters: [
        {fieldname: "view_mode", label: __("Vista"), fieldtype: "Select", options: "Resumen\nDetalle\nConciliación", default: "Resumen", reqd: 1,
            description: __("Conciliación compara saldos completos por empresa y corte; restablece los filtros de detalle."),
            on_change(report) {
                report.cn_display_filters?.();
                if (report.get_filter_value("view_mode") === "Conciliación") {
                    const reset = frappe.query_reports["Estado de Cuenta por Empresa"].bridge_reset();
                    if (Object.entries(reset).some(([key, value]) => (report.get_filter_value(key) || "") !== (value || ""))) {
                        return report.set_filter_value(reset);
                    }
                }
                return report.refresh();
            }},
        {fieldname: "statement_scope", label: __("Sección"), fieldtype: "Select", options: "Todos\nContabilidad del core\nGestión", default: "Todos",
            description: __("Todos muestra pendientes menos créditos y compara con el core. Contabilidad del core muestra los movimientos completos. Gestión muestra solo pendientes menos créditos."),
            on_change(report) {
                const section = report.get_filter_value("statement_scope");
                const component = report.get_filter_value("component") || "";
                const componentSection = component.startsWith("core_") && component !== "core_pending_usd" ? "Contabilidad del core" : "Gestión";
                if ((component && section !== componentSection) || (report.get_filter_value("accounting_queue") && section !== "Gestión")) {
                    return report.set_filter_value({component: "", accounting_queue: 0});
                }
                return report.refresh();
            }},
        {fieldname: "compact_view", label: __("Vista compacta"), fieldtype: "Check", default: 1,
            description: __("En detalle muestra importes con signo y un único saldo al pie. Desmarque para ver el desglose y la trazabilidad de las partidas.")},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "cutoff_date", label: __("Corte histórico al"), fieldtype: "Date",
            description: __("Opcional. Vacío muestra saldos actuales; con fecha excluye operaciones posteriores al corte.")},
        {fieldname: "from_date", label: __("Origen desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Origen hasta"), fieldtype: "Date"},
        {fieldname: "source_account", label: __("Cuenta contable del core"), fieldtype: "Data",
            description: __("Filtra la sección contable. Gestión conserva el alcance por empresa.")},
        {fieldname: "source_currency", label: __("Moneda original del core"), fieldtype: "Select", options: "\nNIO\nUSD"},
        {fieldname: "component", label: __("Componente del saldo"), fieldtype: "Select", options: [
            {label: __("Todos"), value: ""},
            ...[["core_balance_usd", "Saldo contable del core"], ["core_debit_usd", "Débitos originales del core"],
                ["core_credit_usd", "Créditos originales del core"], ["core_balance_nio", "Saldo contable original C$"],
                ["core_debit_nio", "Débitos originales C$"], ["core_credit_nio", "Créditos originales C$"],
                ["management_pending_usd", "Pendientes positivos"], ["other_credit_usd", "Otros créditos pendientes"],
                ["application_pending_usd", "Aplicado sin depósito"], ["core_pending_usd", "Partidas del core pendientes"],
                ["company_credit_usd", "Saldo a favor empresa"], ["client_credit_usd", "Saldo a favor clientes"],
                ["deposit_pending_usd", "Depósitos sin conciliar"], ["company_receivable_usd", "CxC a la empresa por ajustes"], ["employee_receivable_usd", "CxC al empleado por ajustes"]]
                .map(([value, label]) => ({value, label: __(label)}))]},
        {fieldname: "accounting_queue", label: __("Solo complementarias sin registro"), fieldtype: "Check", default: 0,
            description: __("Muestra las partidas complementarias manuales cuyo asiento aún no está confirmado en el core, tengan o no saldo en este reporte.")},
        {fieldname: "status", label: __("Estado de conciliación"), fieldtype: "Autocomplete", options: ["Conciliado", "Pendiente", "Parcial", "Revisar distribución", "Sin conversión US$", "Importado del core; sin confirmar", "Pendiente de cobro", "Parcialmente cobrada", "Revisar cobros", "Solo control contable"],
            description: __("Las sugerencias se actualizan con los estados de las partidas de esta consulta.")},
        {fieldname: "origin_type", label: __("Origen"), fieldtype: "Select", options: "\nCore\nComplementaria manual\nDepósito manual\nPeríodo"},
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
        const advanced = ["cutoff_date", "source_account", "source_currency", "origin_type", "category", "reason_type", "subcategory", "accounting_status", "management_status", "treatment_status", "search"];
        let expanded = false;
        const display = () => {
            const bridge = report.get_filter_value("view_mode") === "Conciliación";
            frappe.query_reports["Estado de Cuenta por Empresa"].filters.forEach(({fieldname:name}) => {
                const control = report.get_filter(name);
                control.$wrapper.toggle(bridge ? ["view_mode", "compact_view", "employer", "cutoff_date"].includes(name)
                    : !advanced.includes(name) || expanded || Boolean(control.get_value()));
            });
        };
        report.cn_display_filters = display;
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
            if (detail.view_mode === "Conciliación") {
                return report.set_filter_value({...frappe.query_reports["Estado de Cuenta por Empresa"].bridge_reset(),
                    view_mode:"Conciliación", employer:detail.employer || ""});
            }
            report.set_filter_value({view_mode: "Detalle", employer: detail.employer || "", component: detail.component || "",
                statement_scope: detail.statement_scope || "Gestión", accounting_queue: detail.accounting_queue ? 1 : 0});
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
        report.cn_display_filters?.();
        const states = report.data?.find(row => Array.isArray(row._available_statuses))?._available_statuses;
        if (states) report.get_filter("status").set_data([...new Set([...states, report.get_filter_value("status")].filter(Boolean))]);
    },
    formatter(value, row, column, data, default_formatter) {
        if (data?._is_statement_total) {
            if (data[column.fieldname] === undefined) return "";
            const total = column.fieldtype === "Currency" && data[column.fieldname] === null
                ? `<span class="text-muted" title="${__("Total sin determinar; hay movimientos sin conversión.")}">—</span>`
                : default_formatter(value, row, column, data);
            return `<strong>${total}</strong>`;
        }
        if (column.fieldname === "statement_amount_nio" && data?.statement_scope === "Gestión") return "";
        if (data?.bridge_row_type && data[column.fieldname] === undefined) return "";
        if (data && column.fieldtype === "Currency" && data[column.fieldname] == null) {
            return `<span class="text-muted" title="${__("Importe o signo sin determinar; revise el detalle.")}">—</span>`;
        }
        const html = default_formatter(value, row, column, data);
        if (data?.bridge_row_type) return html;
        if (data?.employer && column.fieldname === "reconciliation_difference_usd") {
            const args = frappe.utils.escape_html(JSON.stringify({employer:data.employer, view_mode:"Conciliación"}));
            return `<button type="button" class="cn-statement-detail" data-detail="${args}" title="${__("Explicar diferencia y registro pendiente")}" style="border:0;background:transparent;color:var(--text-color);font:inherit;text-decoration:underline;cursor:pointer">${html}</button>`;
        }
        if (data && !data.source_document && data.employer &&
                ((column.fieldtype === "Currency" && Number(value)) || ["pending_count", "core_movement_count"].includes(column.fieldname) ||
                 (column.fieldname === "accounting_pending_count" && Number(value)))) {
            const accounting = ["core_debit_usd", "core_credit_usd", "core_balance_usd", "core_debit_nio", "core_credit_nio", "core_balance_nio", "core_movement_count"].includes(column.fieldname);
            const args = frappe.utils.escape_html(JSON.stringify({employer: data.employer,
                component: column.fieldtype === "Currency" && column.fieldname !== "management_net_usd" ? column.fieldname : "",
                statement_scope: accounting ? "Contabilidad del core" : column.fieldname === "management_net_usd" ? "Todos" : "Gestión",
                accounting_queue: column.fieldname === "accounting_pending_count" ? 1 : 0}));
            return `<button type="button" class="cn-statement-detail" data-detail="${args}" title="${__(accounting ? "Ver movimientos originales del core" : "Ver partidas por gestionar")}" style="border:0;background:transparent;color:var(--text-color);font:inherit;text-decoration:underline;cursor:pointer">${html}</button>`;
        }
        return html;
    },
};
