frappe.listview_settings["CN Complementary Item"] = {
    add_fields: ["reconciliation_status", "applied_usd", "pending_usd", "accounting_status", "category", "status", "review_status", "accounting_source_key", "docstatus", "compensation_status", "credit_management_status", "credit_pending_usd", "result", "origin_deposit", "amount_usd", "usd_currency"],
    has_indicator_for_draft: true,
    has_indicator_for_cancelled: true,
    get_indicator(doc) {
        const state = doc.reconciliation_status || "Pendiente";
        return [__(state), {Conciliado: "green", Parcial: "orange", Pendiente: "red",
            "Revisar distribución": "red", "Vinculado a depósito": "blue"}[state] || "gray",
            `reconciliation_status,=,${state}`];
    },
    formatters: {
        amount_usd(value, df, doc) {
            return cnComplementaryListAmount(doc, "original_usd", df);
        },
        applied_usd(value, df, doc) {
            return cnComplementaryStoredAmount(value, df, doc);
        },
        pending_usd(value, df, doc) {
            return cnComplementaryStoredAmount(value, df, doc);
        },
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
        cnComplementaryListColumns(listview);
        // Frappe 15 does not await before_render. Read the visible page once,
        // then redraw locally (render does not invoke before_render again).
        listview.settings.before_render = () => {
            const sequence = (listview.cn_balance_sequence || 0) + 1;
            listview.cn_balance_sequence = sequence;
            const rows = listview.data || [];
            if (!rows.length) return;
            rows.forEach(row => {
                delete row._cn_balance; delete row._cn_balance_error;
            });
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
                rows.forEach(row => {
                    row._cn_balance = message?.[row.name]; row._cn_balance_error = !row._cn_balance;
                });
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

function cnComplementaryListAmount(doc, key, df) {
    const value = doc._cn_balance?.[key];
    if (value == null || !Number.isFinite(Number(value))) {
        const label = doc._cn_balance_error ? __("Saldo no disponible") : __("Consultando conciliación");
        return `<span title="${frappe.utils.escape_html(label)}">—</span>`;
    }
    return frappe.format(value, {...df, fieldtype: "Currency", options: "usd_currency", precision: 2}, null,
        {...doc, usd_currency: "USD"});
}

function cnComplementaryStoredAmount(value, df, doc) {
    if (doc.reconciliation_status === "Saldo no disponible" || value == null || !Number.isFinite(Number(value))) {
        return `<span title="${frappe.utils.escape_html(__("Saldo no disponible"))}">—</span>`;
    }
    return frappe.format(value, {...df, fieldtype: "Currency", options: "usd_currency", precision: 2}, null,
        {...doc, usd_currency: "USD"});
}

function cnComplementaryListColumns(listview) {
    if (listview.cn_balance_columns_configured || !listview.setup_columns || !listview.list_view_settings) return;
    listview.cn_balance_columns_configured = true;
    const applied = "applied_usd", pending = "pending_usd";
    const financial = new Set([applied, pending, "cn_list_applied_usd", "cn_list_pending_usd"]);
    // Keep existing saved list layouts, replacing the earlier display-only keys.
    const saved = listview.list_view_settings.fields;
    if (saved) {
        const fields = JSON.parse(saved).map(field => ({...field,
            fieldname: ({cn_list_applied_usd: applied, cn_list_pending_usd: pending})[field.fieldname] || field.fieldname}));
        listview.list_view_settings.fields = JSON.stringify(fields.filter((field, index) => fields.findIndex(other => other.fieldname === field.fieldname) === index));
    }
    const apply = () => {
        if (!listview.columns) return;
        const columns = listview.columns.filter(column => !financial.has(column.df?.fieldname));
        let index = columns.findIndex(column => column.df?.fieldname === "amount_usd");
        const amount = index >= 0 ? columns.splice(index, 1)[0] : {type: "Field", df: {fieldname: "amount_usd"}};
        if (index < 0) index = columns.length;
        const currency = {fieldtype: "Currency", options: "usd_currency", precision: 2, read_only: 1};
        columns.splice(index, 0,
            {...amount, df: {...amount.df, ...currency, label: __("Importe USD")}},
            {type: "Field", df: {...currency, fieldname: applied, label: __("Aplicado USD")}},
            {type: "Field", df: {...currency, fieldname: pending, label: __("Pendiente USD")}});
        listview.columns = columns;
    };
    // Both monetary columns are real fields, so native filtering and sorting
    // use the same persisted values shown in the list.
    const setup = listview.setup_columns;
    listview.setup_columns = function(...args) {
        const result = setup.apply(this, args);
        if (result?.then) return result.then(() => {apply();});
        apply();
        return result;
    };
    const count = Math.max((listview.meta?.fields || []).filter(field => field.in_list_view).length,
        JSON.parse(listview.list_view_settings.fields || "[]").length) + 5;
    listview.list_view_settings.total_fields = Math.max(Number(listview.list_view_settings.total_fields) || 0, count);
    const result = listview.setup_columns();
    if (result?.then) result.then(() => listview.render_header(true));
    else listview.render_header(true);
}
