frappe.provide("credinomina");

credinomina.openAccountingBulk = function (onComplete) {
    const api = "credinomina_reconciliation.bulk_accounting_import.";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const storageKey = `cn-accounting-bulk:${frappe.session.user}`;
    let token = localStorage.getItem(storageKey);
    let timer, closed = false, running = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Carga masiva de movimientos contables"), size: "extra-large",
        fields: [
            {fieldtype: "HTML", options: `<p>${__("Cargue un archivo de varios meses. Se creará una importación por empresa y fecha exacta de aplicación, sin modificar documentos existentes ni conciliar automáticamente.")}</p>`},
            {fieldtype: "Attach", fieldname: "source_file", label: __("Archivo de movimientos contables"), reqd: 1},
            {fieldtype: "Select", fieldname: "currency", label: __("Moneda del archivo"), options: "\nUSD\nNIO", reqd: 1},
            {fieldtype: "Float", fieldname: "manual_fx_rate", label: __("Tipo de cambio C$ por US$"), precision: 8,
                depends_on: "eval:doc.currency == 'NIO'", mandatory_depends_on: "eval:doc.currency == 'NIO'",
                description: __("Una sola tasa para toda la carga. Si cambia entre fechas, divida el archivo en cargas con la misma tasa.")},
            {fieldtype: "Column Break"},
            {fieldtype: "Link", fieldname: "employer", label: __("Empresa predeterminada (opcional)"), options: "CN Employer",
                description: __("Déjela vacía si el archivo contiene varias empresas. Solo completa movimientos sin empresa identificada; no reemplaza la empresa de la cartera.")},
            {fieldtype: "Link", fieldname: "portfolio_snapshot", label: __("Corte de cartera (opcional)"), options: "CN Credit Portfolio Snapshot",
                get_query: () => ({filters: {status: ["in", ["Importado", "Importado con alertas"]]}}),
                description: __("Vacío: corte del mes de cada movimiento o el anterior disponible. Seleccione otro corte si lo necesita para el histórico.")},
            {fieldtype: "Check", fieldname: "historical_backfill", label: __("Forzar tratamiento histórico"),
                description: __("Las fechas anteriores a septiembre de 2026 ya se tratan como históricas. No se crean ni se cierran períodos en esta carga.")},
            {fieldtype: "Section Break"},
            {fieldtype: "HTML", fieldname: "result"},
        ],
        primary_action_label: __("Analizar archivo"),
        primary_action: preview,
        onhide() { closed = true; clearTimeout(timer); },
    });
    const result = dialog.fields_dict.result.$wrapper;
    function lockOptions(locked) {
        for (const field of ["source_file", "currency", "manual_fx_rate", "employer", "portfolio_snapshot", "historical_backfill"]) {
            dialog.set_df_property(field, "read_only", locked ? 1 : 0);
        }
    }
    function showError(error) {
        running = false;
        result.html(`<div class="alert alert-danger">${esc(error)}</div>`);
        dialog.set_primary_action(__("Analizar archivo"), preview);
        dialog.get_primary_btn().prop("disabled", false);
    }
    function table(headers, rows) {
        return `<div style="max-height:45vh;overflow:auto"><table class="table table-bordered"><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    function renderSummary(summary) {
        let html = `<p><strong>${__("Documentos nuevos")}: ${summary.groups.length} · ${__("Aplicaciones nuevas")}: ${summary.rows}</strong></p>`;
        html += table([__("Empresa"), __("Fecha de aplicación"), __("Filas"), __("Total US$")], summary.groups.map(group => [
            esc(group.employer), esc(frappe.datetime.str_to_user(group.event_date)), esc(group.count), esc(format_currency(group.total_usd, "USD")),
        ]));
        for (const [key, label] of [["issues", __("Filas que requieren corrección")], ["duplicates", __("Duplicados omitidos")], ["excluded", __("Movimientos que no son aplicaciones (no se importan)")]]) {
            if (!summary[`${key}_count`]) continue;
            html += `<details${key === "issues" ? " open" : ""}><summary>${esc(label)}: ${summary[`${key}_count`]}</summary>`;
            html += table([__("Fila del archivo"), __("Motivo")], summary[key].map(row => [esc(row.row), esc(row.reason)]));
            if (summary[`${key}_count`] > 100) html += `<p>${__("Se muestran las primeras 100 filas.")}</p>`;
            html += "</details>";
        }
        if (summary.issues_count) html += `<p class="text-danger">${__("Corrija estas filas o los alias de las empresas y vuelva a analizar. No se creará ningún documento mientras existan errores.")}</p>`;
        html += `<p class="text-muted">${__("Solo se muestran movimientos reconocidos por el importador contable. Totales, encabezados y otras líneas del reporte no generan aplicaciones.")}</p>`;
        result.html(html);
    }
    async function preview() {
        if (running) return;
        const values = dialog.get_values();
        if (!values) return;
        if (values.currency !== "NIO") values.manual_fx_rate = 0;
        running = true;
        lockOptions(true);
        dialog.get_primary_btn().prop("disabled", true);
        try {
            const response = await frappe.call({method: api + "preview_bulk_import", args: values});
            token = response.message.token;
            localStorage.setItem(storageKey, token);
            await poll();
        } catch (error) { showError(__("No se pudo iniciar el análisis. Revise el mensaje del servidor e intente nuevamente.")); }
    }
    async function confirm() {
        if (running) return;
        running = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: api + "confirm_bulk_import", args: {token}});
            await poll();
        } catch (error) { showError(__("No se pudo confirmar la carga. Revise el mensaje del servidor.")); }
    }
    async function poll() {
        if (closed) return;
        try {
            const response = await frappe.call({method: api + "get_bulk_import_status", args: {token}});
            const state = response.message;
            if (closed) return;
            if (state.options) await dialog.set_values(state.options);
            if (["En cola", "Procesando"].includes(state.status)) {
                running = true;
                lockOptions(true);
                dialog.get_primary_btn().prop("disabled", true);
                result.html(`<div class="alert alert-info"><strong>${esc(__(state.status))}</strong><br>${esc(state.progress || (state.phase === "preview" ? __("Analizando empresa, fechas y duplicados…") : __("Creando las importaciones…")))}<br>${__("Puede cerrar esta ventana y volver con Carga masiva para consultar el resultado. Si permanece en cola, revise los workers de la cola long.")}</div>`);
                timer = setTimeout(poll, 2500);
            } else if (state.status === "Vista previa") {
                running = false;
                lockOptions(true);
                renderSummary(state.summary);
                dialog.set_primary_action(__("Crear importaciones"), confirm);
                dialog.get_primary_btn().prop("disabled", !!state.summary.issues_count || !state.summary.rows);
            } else if (state.status === "Completado") {
                running = false;
                result.html(`<div class="alert alert-success">${__("Carga completada. Abra cada importación, revise sus filas y use Conciliar esta empresa cuando corresponda.")}</div>` + table(
                    [__("Importación"), __("Empresa"), __("Fecha"), __("Filas"), __("Total US$")], state.created.map(doc => [
                        `<a href="/app/cn-accounting-import/${encodeURIComponent(doc.name)}">${esc(doc.name)}</a>`, esc(doc.employer),
                        esc(frappe.datetime.str_to_user(doc.event_date)), esc(doc.rows), esc(format_currency(doc.total_usd, "USD")),
                    ])));
                dialog.set_primary_action(__("Cerrar"), () => dialog.hide());
                dialog.get_primary_btn().prop("disabled", false);
                if (onComplete) onComplete();
            } else { showError(state.error || __("No se pudo completar la carga.")); }
        } catch (error) { showError(__("No se pudo consultar la carga. Cierre y vuelva a abrir Carga masiva para reintentar. Si expiró, analice el archivo otra vez.")); }
    }
    dialog.set_secondary_action(() => {
        if (running) { frappe.msgprint(__("Espere a que termine la carga en curso.")); return; }
        token = null;
        localStorage.removeItem(storageKey);
        result.empty();
        lockOptions(false);
        dialog.set_primary_action(__("Analizar archivo"), preview);
        dialog.get_primary_btn().prop("disabled", false);
    });
    dialog.set_secondary_action_label(__("Nuevo análisis"));
    dialog.show();
    if (token) poll();
};
