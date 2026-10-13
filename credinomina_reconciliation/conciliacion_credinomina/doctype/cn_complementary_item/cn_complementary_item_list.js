frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["accounting_status", "category", "status", "review_status", "accounting_source_key", "docstatus", "compensation_status", "credit_management_status", "credit_pending_usd", "result"],
    has_indicator_for_draft: true,
    get_indicator(doc) {
        if (doc.docstatus === 2) return [__("Cancelada"), "gray", "docstatus,=,2"];
        if (doc.status === "Revertido") return [__("Revertida"), "gray", "status,=,Revertido"];
        const state = (doc._cn_balance || doc.__onload?.cn_balance)?.state;
        return [__(state || (doc._cn_balance_error || doc.__onload?.cn_balance_error ? "Saldo no disponible" : "Consultando conciliación")),
            {Conciliado: "green", Parcial: "orange", Pendiente: "red", "Revisar distribución": "red"}[state] || "gray", ""];
    },
    formatters: {
        accounting_status(value, df, doc) {
            const esc = text => frappe.utils.escape_html(String(text ?? ""));
            const balance = doc._cn_balance;
            const management = balance?.management_status;
            const accounting = __(value || "Pendiente de registro");
            const pending = management ? balance.management_pending_usd : balance?.pending_usd;
            const label = management ? __("Por devolver/aplicar US$") : __("Sin distribuir/compensar US$");
            const lines = [accounting];
            if (management) lines.push(`${__("Gestión")}: ${__(management)}`);
            if (pending != null) lines.push(`${label}: ${Number(pending).toFixed(2)}`);
            if ((balance?.receivable_usd ?? balance?.company_receivable_usd)) lines.push(`${__("Por cobrar US$")}: ${Number(balance.receivable_usd ?? balance.company_receivable_usd).toFixed(2)}`);
            if (balance && (balance.receivable_usd === null || (balance.receivable_usd === undefined && balance.company_receivable_usd === null))) lines.push(__("CxC con cobros por revisar · importe sin determinar"));
            if (balance?.amount_issue) lines.push(__(balance.amount_issue));
            return `<span style="display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;padding:2px 7px;border:1px solid var(--border-color);border-radius:999px;color:var(--text-color);background-color:var(--control-bg, var(--bg-color))" title="${esc(lines.join(" · "))}">${esc(accounting)}</span>`;
        },
    },
    onload(listview) {
        listview.settings ||= frappe.listview_settings["CN Complementary Item"];
        // Frappe 15 does not await before_render. Read the visible page once,
        // then redraw locally (render does not invoke before_render again).
        listview.settings.before_render = () => {
            const sequence = (listview.cn_balance_sequence || 0) + 1;
            listview.cn_balance_sequence = sequence;
            const rows = listview.data || [];
            if (!rows.length) return;
            rows.forEach(row => {delete row._cn_balance; delete row._cn_balance_error;});
            const load = async () => {
                const balances = {};
                for (let offset = 0; offset < rows.length; offset += 500) {
                    if (sequence !== listview.cn_balance_sequence || rows !== listview.data) return {};
                    const {message} = await frappe.call({method: "credinomina_reconciliation.complementary_balances.get_list_balances",
                        args: {names: rows.slice(offset, offset + 500).map(row => row.name)}});
                    Object.assign(balances, message || {});
                }
                return balances;
            };
            load().then(message => {
                if (sequence !== listview.cn_balance_sequence || rows !== listview.data) return;
                rows.forEach(row => {row._cn_balance = message?.[row.name]; row._cn_balance_error = !row._cn_balance;});
                listview.render();
            }).catch(() => {
                if (sequence !== listview.cn_balance_sequence || rows !== listview.data) return;
                rows.forEach(row => {row._cn_balance_error = true;});
                listview.render();
            });
        };
        listview.page.add_inner_button(__("Saldos de empresas pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Saldo a favor de la empresa"],
                ["CN Complementary Item", "credit_pending_usd", ">", 0], ["CN Complementary Item", "docstatus", "=", 1]]);
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("CxC a la empresa por ajustes"), () => {
            frappe.route_options = {view_mode: "Detalle", component: "company_receivable_usd"};
            frappe.set_route("query-report", "Estado de Cuenta por Empresa");
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("CxC al empleado por ajustes"), () => {
            frappe.route_options = {view_mode: "Detalle", component: "employee_receivable_usd"};
            frappe.set_route("query-report", "Estado de Cuenta por Empresa");
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("Saldos de clientes pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Saldo a favor del cliente"],
                ["CN Complementary Item", "credit_pending_usd", ">", 0], ["CN Complementary Item", "docstatus", "=", 1]]);
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("Compensaciones pendientes"), () => {
            listview.filter_area.add([["CN Complementary Item", "category", "=", "Compensación entre partidas"],
                ["CN Complementary Item", "compensation_status", "in", ["Sin compensar", "Compensada parcialmente"]],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("Movimientos por revisar"), () => {
            listview.filter_area.add([["CN Complementary Item", "review_status", "in", ["Pendiente de identificar", "Pendiente de revisión", "No conciliatoria", "Ajuste pendiente de completar", "Ajuste pendiente de confirmar", "Sin compensar", "Compensada parcialmente"]],
                ["CN Complementary Item", "docstatus", "=", 0]]);
        }, __("Vistas rápidas"));
        listview.page.add_inner_button(__("Pendientes de registro"), () => {
            listview.filter_area.add([["CN Complementary Item", "accounting_status", "in", ["Pendiente de registro", "Asiento informado"]],
                ["CN Complementary Item", "docstatus", "!=", 2]]);
        }, __("Vistas rápidas"));
    },
};
