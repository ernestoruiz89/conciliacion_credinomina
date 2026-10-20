frappe.listview_settings["CN Reconciliation Period"] = {
    // Always fetch the indicator, including with personalized list columns.
    // Colors come from DocType states, shared by the list and form header.
    add_fields: ["status", "status_before_close", "applied_total_usd", "deposit_coverage_usd",
        "complementary_coverage_usd", "pending_usd", "usd_currency"],
    formatters: {
        deposit_coverage_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        complementary_coverage_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        pending_usd(value, df, doc) { return value == null ? "—" : frappe.format(value, df, null, doc); },
        status_before_close(value, df, doc) {
            if (doc.status !== "Cerrado") return "—";
            const state = (frappe.get_meta("CN Reconciliation Period").states || [])
                .find(state => state.title === value);
            const color = state ? frappe.scrub(state.color, "-") : "gray";
            return `<span class="indicator-pill ${frappe.utils.escape_html(color)}">${frappe.utils.escape_html(value || __("Sin resultado registrado"))}</span>`;
        },
    },
    onload(listview) {
        if (frappe.model.can_write("CN Reconciliation Period") && frappe.model.can_write("CN Accounting Import")) {
            listview.page.add_inner_button(__("Conciliación Cobranza-Aplicacion"), () => cn_monthly_first_dialog(listview));
        }
        const settings = listview.list_view_settings;
        const hiddenColumns = new Set([
            "reconciliation_mode", "historical_application_date",
            "historical_start_date", "historical_end_date",
        ]);
        // Apply to default and previously personalized lists without hiding
        // the fields in the form or removing their filter/search support.
        for (const field of listview.meta.fields) {
            if (hiddenColumns.has(field.fieldname)) field.in_list_view = 0;
        }
        if (settings.fields) {
            const fields = JSON.parse(settings.fields).filter(field => !hiddenColumns.has(field.fieldname));
            for (const fieldname of ["status_before_close", "applied_total_usd", "deposit_coverage_usd",
                "complementary_coverage_usd", "pending_usd"]) {
                if (!fields.some(field => field.fieldname === fieldname)) fields.push({fieldname});
            }
            settings.fields = JSON.stringify(fields);
        }
        settings.total_fields = Math.max(Number(settings.total_fields) || 0,
            listview.meta.fields.filter(field => field.in_list_view).length + 3);
        listview.setup_columns();
        listview.render_header(true);
    },
};

function cn_monthly_error(error) {
    try {
        const messages = JSON.parse(error?._server_messages || error?.responseJSON?._server_messages || "[]");
        if (messages.length) {
            const message = typeof messages[0] === "string" ? JSON.parse(messages[0]) : messages[0];
            if (message.message) return String(message.message).replace(/<[^>]*>/g, "");
        }
    } catch (_) { /* Use the plain fallback below. */ }
    return error?.message || __("No se pudo completar. Abra el período para revisar e intente nuevamente.");
}

function cn_monthly_first_dialog(listview) {
    if (listview.cn_monthly_first_dialog) {listview.cn_monthly_first_dialog.show(); return;}
    const api = "credinomina_reconciliation.monthly_first_reconciliation.";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    let busy = false, results = [], completed = 0, month = "";
    function updateProgress() {
        const counts = results.reduce((all, row) => {all[row.state] = (all[row.state] || 0) + 1; return all;}, {});
        const wrapper = dialog.fields_dict.results.$wrapper;
        wrapper.find("[data-progress]").text(`${__("Mes")}: ${month.slice(0, 7)} · ${completed}/${results.length} ${__("procesados")}`);
        wrapper.find("[data-summary]").text(Object.entries(counts).map(([state, count]) => `${__(state)}: ${count}`).join(" · "));
    }
    function updateActions() {
        results.forEach((row, index) => dialog.fields_dict.results.$wrapper.find(`[data-assign="${index}"]`).toggle(!busy && !!row.can_assign));
    }
    function updateRow(index) {
        const row = results[index];
        const element = dialog.fields_dict.results.$wrapper.find(`[data-result-row="${index}"]`);
        element.find("[data-state]").text(__(row.state));
        element.find("[data-assigned]").text(row.assigned ?? "—");
        element.find("[data-detail]").text(row.error || (row.reviewed != null ? __("{0} de {1} filas conformes; {2} con diferencias; {3} sin base.",
            [row.conforming, row.reviewed, row.differences, row.missing_basis]) : ""));
        updateProgress();
    }
    function loadRows() {
        // Build rows only when starting a new month. Progress and retries keep
        // the table, scroll container and existing row nodes in place.
        dialog.fields_dict.results.$wrapper.find("[data-results-body]").html(results.map((row, index) => `<tr data-result-row="${index}">
            <td>${esc(row.employer)}<br><a href="/app/cn-reconciliation-period/${encodeURIComponent(row.name)}" target="_blank" rel="noopener">${esc(row.name)}</a></td>
            <td data-state>${esc(__(row.state))}</td><td data-assigned>—</td><td data-detail></td>
            <td><a href="#" data-assign="${index}" style="display:none">${__("Asignar y reprocesar")}</a></td></tr>`).join(""));
        updateProgress();
    }
    const dialog = new frappe.ui.Dialog({title: __("Conciliación Cobranza-Aplicacion"), size: "extra-large", fields: [
        {fieldname: "payroll_month", fieldtype: "Date", label: __("Mes de cobranza"), reqd: 1,
            default: frappe.datetime.now_date().slice(0, 7) + "-01",
            description: __("Seleccione una fecha del mes. Se procesarán todos sus períodos accesibles, sin limitarse a la página o filtros de la lista.")},
        {fieldname: "results", fieldtype: "HTML"},
    ], primary_action_label: __("Conciliar mes"), async primary_action(values) {
        if (busy) return;
        month = values.payroll_month.slice(0, 7) + "-01";
        busy = true;
        dialog.disable_primary_action();
        dialog.set_df_property("payroll_month", "read_only", 1);
        results = [];
        completed = 0;
        loadRows();
        try {
            const response = await frappe.call({method: api + "list_periods", args: {payroll_month: month}});
            results = (response.message || []).map(row => ({...row, state: "Pendiente"}));
            completed = 0;
            loadRows();
            for (let index = 0; index < results.length; index++) {
                const row = results[index];
                row.state = "Procesando";
                updateRow(index);
                try {
                    const response = await frappe.call({method: api + "reconcile_period", type: "POST",
                        args: {period_name: row.name, payroll_month: month}});
                    results[index] = {...row, ...response.message};
                } catch (error) {
                    results[index] = {...row, state: "Error", error: cn_monthly_error(error)};
                }
                completed++;
                updateRow(index);
            }
            if (!results.length) frappe.msgprint(__("No hay períodos accesibles para el mes seleccionado."));
            else frappe.show_alert({message: __("Conciliación mensual finalizada. Revise el resultado de cada convenio."), indicator: "blue"});
            listview.refresh();
        } finally {
            busy = false;
            dialog.enable_primary_action();
            dialog.set_df_property("payroll_month", "read_only", 0);
            updateActions();
        }
    }});
    dialog.$wrapper.find(".modal-dialog").css({width: "96vw", maxWidth: "1600px"});
    dialog.fields_dict.results.$wrapper.html(`<p data-progress></p><p data-summary></p>
        <div data-results-scroll style="max-height:65vh;overflow:auto"><table class="table table-bordered" style="table-layout:fixed">
        <thead style="position:sticky;top:0;background:var(--modal-bg, var(--card-bg, white));z-index:1"><tr>
        <th style="width:28%">${__("Convenio / período")}</th><th style="width:20%">${__("Resultado")}</th>
        <th style="width:10%">${__("Aplicaciones asignadas")}</th><th style="width:27%">${__("Detalle")}</th><th style="width:15%">${__("Acción")}</th>
        </tr></thead><tbody data-results-body style="overflow-wrap:anywhere"></tbody></table></div>`);
    dialog.fields_dict.results.$wrapper.on("click", "[data-assign]", async event => {
        event.preventDefault();
        if (busy) return;
        const index = Number(event.currentTarget.dataset.assign);
        const row = results[index];
        if (!row?.can_assign) return;
        busy = true;
        updateActions();
        dialog.disable_primary_action();
        dialog.set_df_property("payroll_month", "read_only", 1);
        try {
            await cn_monthly_assign_dialog(row.name, updated => {
                results[index] = {...row, ...updated};
                updateRow(index);
                listview.refresh();
            });
        } finally {
            busy = false;
            dialog.enable_primary_action();
            dialog.set_df_property("payroll_month", "read_only", 0);
            updateActions();
        }
    });
    listview.cn_monthly_first_dialog = dialog;
    dialog.show();
}

async function cn_monthly_assign_dialog(period, onUpdated) {
    const api = "credinomina_reconciliation.monthly_first_reconciliation.";
    const response = await frappe.call({method: api + "get_unassigned_imports", args: {period_name: period}});
    const data = response.message;
    if (!data.imports.length) {
        frappe.msgprint(__("No hay importaciones con aplicaciones operativas pendientes de asignar que pueda editar para esta empresa. Revise o cargue los movimientos en CN Accounting Import."));
        return;
    }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    return new Promise(resolve => {
        let saving = false;
        const dialog = new frappe.ui.Dialog({title: __("Asignar aplicaciones y reprocesar"), size: "extra-large", fields: [
            {fieldtype: "HTML", options: `<p><strong>${esc(data.employer)} · ${esc(data.period)}</strong></p><p>${__("Seleccione las importaciones. Se asignarán sus aplicaciones pendientes al período indicado. Revise las fechas e importes, especialmente cuando correspondan a otro mes.")}</p>`},
            {fieldname: "imports", fieldtype: "Table", cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
                data: data.imports.map(row => ({...row, import_name: row.name, selected: 0, date_scope: row.same_month ? __("Mismo mes") : __("Revisar mes")})), fields: [
                    {fieldname: "selected", fieldtype: "Check", label: __("Asignar"), in_list_view: 1, columns: 1},
                    {fieldname: "import_name", fieldtype: "Link", options: "CN Accounting Import", label: __("Importación"), read_only: 1, in_list_view: 1, columns: 3},
                    {fieldname: "start_date", fieldtype: "Date", label: __("Desde"), read_only: 1, in_list_view: 1, columns: 1},
                    {fieldname: "end_date", fieldtype: "Date", label: __("Hasta"), read_only: 1, in_list_view: 1, columns: 1},
                    {fieldname: "count", fieldtype: "Int", label: __("Aplicaciones"), read_only: 1, in_list_view: 1, columns: 1},
                    {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", label: __("Importe US$"), precision: 2, read_only: 1, in_list_view: 1, columns: 2},
                    {fieldname: "date_scope", fieldtype: "Data", label: __("Mes"), read_only: 1, in_list_view: 1, columns: 1},
                ]},
        ], primary_action_label: __("Asignar y reprocesar"), async primary_action() {
            if (saving) return;
            const selected = dialog.fields_dict.imports.df.data.filter(row => row.selected);
            if (!selected.length) {frappe.msgprint(__("Seleccione al menos una importación.")); return;}
            saving = true;
            dialog.disable_primary_action();
            try {
                const response = await frappe.call({method: api + "assign_and_reconcile", type: "POST", freeze: true,
                    args: {period_name: period, modified: data.modified,
                        selections: JSON.stringify(selected.map(row => ({name: row.import_name, modified: row.modified, applications: row.applications})))}});
                onUpdated(response.message);
                dialog.hide();
            } finally {saving = false; dialog.enable_primary_action();}
        }, onhide: () => resolve()});
        dialog.$wrapper.find(".modal-dialog").css({width: "96vw", maxWidth: "1600px"});
        dialog.show();
    });
}
