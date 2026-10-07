frappe.credinomina = frappe.credinomina || {};

frappe.credinomina.openAccountingBulk = function (onComplete) {
    const api = "credinomina_reconciliation.bulk_accounting_import.";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const storageKey = `cn-accounting-bulk:${frappe.session.user}`;
    const choicesKey = storageKey + ":employers";
    let choices = {}, choiceFile = "", choiceHash = "", choicesDirty = false, previewRows = new Map();
    try {
        const saved = JSON.parse(localStorage.getItem(choicesKey) || "null");
        if (saved) ({choices, choiceFile, choiceHash, choicesDirty} = saved);
    } catch (_) { localStorage.removeItem(choicesKey); }
    let token = localStorage.getItem(storageKey);
    let timer, closed = false, running = false, currentPhase = "preview";
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
            {fieldtype: "Link", fieldname: "portfolio_snapshot", label: __("Corte de cartera"), options: "CN Credit Portfolio Snapshot", reqd: 1,
                get_query: () => ({filters: {disabled: 0, status: ["in", ["Importado", "Importado con alertas"]]}}),
                description: __("Obligatorio. Seleccione el corte importado para identificar los créditos y clientes de esta carga.")},
            {fieldtype: "Check", fieldname: "historical_backfill", label: __("Forzar tratamiento histórico"),
                description: __("Las fechas anteriores a septiembre de 2026 ya se tratan como históricas. No se crean ni se cierran períodos en esta carga.")},
            {fieldtype: "Section Break"},
            {fieldtype: "Button", fieldname: "recover_batch", label: __("Recuperar última carga guardada"), click: async () => {
                if (running) return;
                try {
                    const response = await frappe.call({method: api + "latest_bulk_import"});
                    if (!response.message?.token) { frappe.msgprint(__("No tiene cargas guardadas.")); return; }
                    token = response.message.token;
                    localStorage.setItem(storageKey, token);
                    await poll();
                } catch (_) { showError(__("No se pudo recuperar la carga. Revise la conexión e intente nuevamente.")); }
            }},
            {fieldtype: "HTML", fieldname: "result"},
        ],
        primary_action_label: __("Analizar archivo"),
        primary_action: preview,
        onhide() { closed = true; clearTimeout(timer); },
    });
    const result = dialog.fields_dict.result.$wrapper;
    function saveChoices() {
        localStorage.setItem(choicesKey, JSON.stringify({choices, choiceFile, choiceHash, choicesDirty}));
    }
    async function saveDraftChoices() {
        if (!token || !choiceHash) return;
        try {
            await frappe.call({method: api + "save_bulk_employer_choices", args: {
                token, employer_assignments: JSON.stringify(choices), file_hash: choiceHash,
            }});
        } catch (_) {
            frappe.msgprint(__("Las selecciones se conservaron en este navegador, pero no se pudieron guardar en el servidor. Vuelva a analizar para guardarlas con la carga."));
        }
    }
    const attr = value => esc(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
    function companyChoice(row) {
        previewRows.set(String(row.row), row);
        const label = choices[String(row.row)] || "NO IDENTIFICADA";
        return `<span tabindex="0" title="${attr(row.description || __("Sin descripción del asiento"))}" style="cursor:help;border-bottom:1px dotted currentColor">${esc(label)}</span><br><button type="button" class="btn btn-xs btn-default cn-change-employer" data-source-row="${attr(row.row)}">${__("Cambiar empresa")}</button> <button type="button" class="btn btn-xs btn-default cn-add-employer-alias" data-source-row="${attr(row.row)}">${__("Agregar alias")}</button>`;
    }
    result.on("click", ".cn-add-employer-alias", function () {
        if (running || currentPhase === "create") return;
        const number = this.getAttribute("data-source-row");
        const row = previewRows.get(number);
        let saving = false;
        const picker = new frappe.ui.Dialog({
            title: __("Agregar alias a una empresa"),
            fields: [
                {fieldtype: "HTML", options: `<p>${__("Busque la empresa a la que pertenece el nombre del archivo. El alias se usará también en próximas cargas.")}</p>${row ? `<div style="white-space:pre-wrap;max-height:180px;overflow:auto">${esc(row.description || "")}</div>` : ""}`},
                {fieldtype: "Link", fieldname: "employer", label: __("Empresa"), options: "CN Employer", reqd: 1,
                    get_query: () => ({filters: {name: ["!=", "NO IDENTIFICADA"]}})},
                {fieldtype: "Data", fieldname: "alias_name", label: __("Alias / nombre en el archivo"), reqd: 1,
                    default: row?.employer_text || ""},
            ],
            primary_action_label: __("Guardar alias y volver a analizar"),
            async primary_action(values) {
                if (saving || running || !values.employer || !values.alias_name?.trim()) return;
                saving = true;
                running = true;
                picker.get_primary_btn().prop("disabled", true);
                dialog.get_primary_btn().prop("disabled", true);
                try {
                    await frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_employer.cn_employer.add_employer_alias",
                        args: {employer: values.employer, alias_name: values.alias_name},
                    });
                    choicesDirty = true;
                    saveChoices();
                    picker.hide();
                    frappe.show_alert({message: __("Alias guardado. Actualizando el análisis del archivo."), indicator: "green"});
                    running = false;
                    if (!closed) await preview();
                } catch (_) {
                    running = false;
                    dialog.get_primary_btn().prop("disabled", choicesDirty || !lastSummary?.sections || !!lastSummary?.issues_count);
                } finally {
                    saving = false;
                    picker.get_primary_btn().prop("disabled", false);
                }
            },
        });
        picker.show();
    });
    result.on("click", ".cn-change-employer", function () {
        if (running) return;
        const number = this.getAttribute("data-source-row");
        const row = previewRows.get(number);
        if (!row) return;
        const picker = new frappe.ui.Dialog({
            title: __("Asignar empresa al movimiento"),
            fields: [
                {fieldtype: "HTML", options: `<p>${__("Fila")}: ${esc(number)}</p><div style="white-space:pre-wrap;max-height:220px;overflow:auto">${esc(row.description || __("Sin descripción del asiento"))}</div>`},
                {fieldtype: "Link", fieldname: "employer", label: __("Empresa"), options: "CN Employer",
                    default: choices[number] || "", get_query: () => ({filters: {name: ["!=", "NO IDENTIFICADA"]}}),
                    description: __("Deje vacío para mantener NO IDENTIFICADA. La selección se aplicará al volver a analizar este archivo.")},
            ],
            primary_action_label: __("Guardar selección"),
            async primary_action(values) {
                if (values.employer) choices[number] = values.employer;
                else delete choices[number];
                choicesDirty = true;
                saveChoices();
                picker.hide();
                renderSummary(lastSummary);
                dialog.get_primary_btn().prop("disabled", true);
                await saveDraftChoices();
            },
        });
        picker.show();
    });
    let lastSummary;
    result.on("click", ".cn-clear-employers", async function () {
        if (running || currentPhase === "create") return;
        choices = {}; choicesDirty = true;
        saveChoices();
        if (lastSummary) {
            renderSummary(lastSummary);
            dialog.get_primary_btn().prop("disabled", true);
        } else {
            result.empty();
            dialog.set_primary_action(__("Analizar archivo"), preview);
            dialog.get_primary_btn().prop("disabled", false);
        }
        await saveDraftChoices();
    });
    function lockOptions(locked) {
        for (const field of ["source_file", "currency", "manual_fx_rate", "employer", "portfolio_snapshot", "historical_backfill"]) {
            dialog.set_df_property(field, "read_only", locked ? 1 : 0);
        }
    }
    function showError(error) {
        running = false;
        result.html(`<div class="alert alert-danger">${esc(error)}</div>` + (currentPhase !== "create" && Object.keys(choices).length
            ? `<button type="button" class="btn btn-default cn-clear-employers">${__("Quitar selecciones de empresa")}</button>` : ""));
        dialog.set_primary_action(__("Analizar archivo"), preview);
        dialog.get_primary_btn().prop("disabled", false);
    }
    function table(headers, rows) {
        return `<div style="max-height:45vh;overflow:auto"><table class="table table-bordered"><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    }
    function renderSummary(summary) {
        lastSummary = summary;
        previewRows = new Map();
        let html = `<p><strong>${__("Importaciones nuevas")}: ${summary.group_count ?? summary.groups.length} · ${__("Aplicaciones nuevas")}: ${summary.rows} · ${__("Partidas en revisión")}: ${summary.complementary_count || 0}</strong></p>`;
        if (choicesDirty) html += `<p class="alert alert-warning">${__("Hay selecciones de empresa sin analizar. Pulse Nuevo análisis y luego Analizar archivo para reagrupar antes de importar.")}</p>`;
        if (Object.keys(choices).length) html += `<p>${__("Empresas seleccionadas manualmente")}: ${Object.keys(choices).length} <button type="button" class="btn btn-xs btn-default cn-clear-employers">${__("Quitar selecciones de empresa")}</button></p>`;
        if (!summary.sections) {
            html += `<p class="alert alert-warning">${__("Esta vista previa es anterior a la separación por empresa. Pulse Nuevo análisis para ver los casos identificados y no identificados antes de confirmar.")}</p>`;
        } else {
            html += identificationSection(summary.sections.identified, false);
            html += identificationSection(summary.sections.unidentified, true);
        }
        if (summary.duplicates_count) html += `<p class="alert alert-warning">${__("Hay movimientos similares. Revise las filas indicadas: se importarán todos, aunque coincidan cuenta, asiento e importe.")}</p>`;
        for (const [key, label] of [["issues", __("Filas que requieren corrección")], ["duplicates", __("Posibles duplicados (se importarán)")], ["already_imported", __("Filas del mismo archivo ya registradas")], ["excluded", __("Movimientos que no son aplicaciones (no se importan)")]]) {
            if (!summary[`${key}_count`]) continue;
            html += `<details${key === "issues" || key === "duplicates" ? " open" : ""}><summary>${esc(label)}: ${summary[`${key}_count`]}</summary>`;
            html += table([__("Fila del archivo"), __("Motivo")], summary[key].map(row => [esc(row.row), esc(row.reason)]));
            if (summary[`${key}_count`] > 100) html += `<p>${__("Se muestran las primeras 100 filas.")}</p>`;
            html += "</details>";
        }
        if (summary.issues_count) html += `<p class="text-danger">${__("Corrija estas filas o los alias de las empresas y vuelva a analizar. No se creará ningún documento mientras existan errores.")}</p>`;
        html += `<p class="text-muted">${__("Solo se muestran movimientos reconocidos por el importador contable. Totales, encabezados y otras líneas del reporte no generan aplicaciones.")}</p>`;
        result.html(html);
    }
    function identificationSection(section, unidentified) {
        const count = section.rows + section.complementary_count + section.deposit_count;
        const title = unidentified ? __("Casos no identificados") : __("Empresas identificadas");
        let html = `<section data-identification="${unidentified ? "unidentified" : "identified"}" class="mb-4"><h4>${title} · ${esc(count)} ${__("movimientos")}</h4>`;
        if (!count) return html + `<p class="text-muted">${__("No hay movimientos en este grupo.")}</p></section>`;
        if (unidentified) html += `<p class="alert alert-warning">${__("Los casos sin empresa se cargarán como NO IDENTIFICADA; se creará al confirmar si no existe. Cada aplicación tendrá su propia importación, aunque coincida la fecha, para corregir la empresa caso por caso. Se conservarán los datos originales.")}</p><p><button type="button" class="btn btn-sm btn-default cn-add-employer-alias">${__("Agregar alias")}</button></p>`;
        html += `<p><strong>${__("Aplicaciones")}: ${esc(section.rows)} · ${__("Importaciones")}: ${esc(section.group_count ?? section.groups.length)} · ${__("Total aplicado US$")}: ${esc(format_currency(section.application_total_usd, "USD"))}</strong><br>${__("Partidas en revisión")}: ${esc(section.complementary_count)} · ${__("Depósitos detectados")}: ${esc(section.deposit_count)}</p>`;
        if (section.rows && unidentified) {
            html += table([__("Fila"), __("Empresa asignada"), __("Fecha"), __("Cliente"), __("Crédito"), __("Empresa en archivo"), __("Asiento"), __("US$"), __("Validación de cartera")], section.applications.map(row => [
                esc(row.row), companyChoice(row), esc(row.event_date ? frappe.datetime.str_to_user(row.event_date) : "—"),
                esc(row.client_name || "—"), esc(row.loan_number || "—"), esc(row.employer_text || __("Sin dato")),
                esc(row.voucher || "—"), esc(format_currency(row.total_usd, "USD")), esc(row.reason || __("Sin empresa identificada")),
            ]));
            if (section.rows > section.applications.length) html += `<p>${__("Se muestran las primeras 100 aplicaciones; las cantidades y totales incluyen todos los casos.")}</p>`;
        } else if (section.rows) {
            html += table([__("Empresa"), __("Fecha de aplicación"), __("Filas"), __("Total US$")], section.groups.map(group => [
                esc(group.employer), esc(frappe.datetime.str_to_user(group.event_date)), esc(group.count), esc(format_currency(group.total_usd, "USD")),
            ]));
            if (section.group_count > section.groups.length) html += `<p>${__("Se muestran las primeras 100 importaciones; los totales incluyen todas.")}</p>`;
        }
        if (section.complementary_count) {
            html += `<h5>${__("Partidas complementarias en borrador")}</h5><p>${__("No afectan saldos ni depósitos hasta confirmar su tratamiento.")}</p>`;
            html += table([__("Fila"), ...(unidentified ? [__("Empresa asignada")] : []), __("Clasificación"), unidentified ? __("Empresa en archivo") : __("Empresa"), __("Motivo")], section.complementary.map(row => [
                esc(row.row), ...(unidentified ? [companyChoice(row)] : []), esc(row.classification), esc((unidentified ? row.employer_text : row.employer) || __("Pendiente de identificar")), esc(row.reason),
            ]));
            if (section.complementary_count > section.complementary.length) html += `<p>${__("Se muestran las primeras 100 filas.")}</p>`;
        }
        if (section.deposit_count) {
            html += `<h5>${__("Depósitos detectados")}</h5><p>${__("Se crearán en borrador o se vincularán a los ya importados. Revise empresa, cuenta e importe antes de confirmar; no se concilian automáticamente.")}</p>`;
            html += table([__("Fila"), ...(unidentified ? [__("Empresa asignada")] : []), unidentified ? __("Empresa en archivo") : __("Empresa"), __("Referencia"), __("Importe bancario")], section.deposits.map(row => [
                esc(row.row), ...(unidentified ? [companyChoice(row)] : []), esc((unidentified ? row.employer_text : row.employer) || __("Por identificar")), esc(row.reference), esc(format_currency(row.amount, row.currency)),
            ]));
            if (section.deposit_count > section.deposits.length) html += `<p>${__("Se muestran las primeras 100 filas.")}</p>`;
        }
        return html + "</section>";
    }
    async function preview() {
        if (running) return;
        const values = dialog.get_values();
        if (!values) return;
        if (values.source_file !== choiceFile) {
            choices = {}; choiceHash = ""; choicesDirty = false;
        }
        choiceFile = values.source_file;
        values.employer_assignments = JSON.stringify(choices);
        values.assignments_file_hash = choiceHash;
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
        if (running || choicesDirty) return;
        running = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: api + "confirm_bulk_import", args: {token}});
            await poll();
        } catch (error) { showError(__("No se pudo confirmar la carga. Revise el mensaje del servidor.")); }
    }
    async function resume() {
        if (running) return;
        running = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: api + "resume_bulk_import", args: {token}});
            await poll();
        } catch (_) {
            showError(__("No se pudo reanudar. Si el trabajo sigue activo, espere; consulte su estado antes de volver a intentar."));
            dialog.set_primary_action(__("Consultar estado"), poll);
        }
    }
    async function poll() {
        if (closed) return;
        try {
            const response = await frappe.call({method: api + "get_bulk_import_status", args: {token}});
            const state = response.message;
            if (closed) return;
            currentPhase = state.phase || "preview";
            if (state.options) await dialog.set_values(state.options);
            if (state.options && (!choicesDirty || (choiceFile && choiceFile !== state.options.source_file))) {
                choices = state.draft_assignments ?? state.options.employer_assignments ?? {};
                choiceFile = state.options.source_file;
                choiceHash = state.options.assignments_file_hash || state.summary?.file_hash || "";
                choicesDirty = JSON.stringify(choices) !== JSON.stringify(state.options.employer_assignments || {});
                saveChoices();
            }
            if (["En cola", "Procesando"].includes(state.status)) {
                running = true;
                lockOptions(true);
                dialog.get_primary_btn().prop("disabled", true);
                result.html(`<div class="alert alert-info"><strong>${esc(__(state.status))}</strong><br>${esc(state.progress || (state.phase === "preview" ? __("Analizando empresa, fechas y duplicados…") : __("Creando las importaciones…")))}<br>${__("Puede cerrar esta ventana y volver con Carga masiva para consultar el resultado. Si permanece en cola, revise los workers de la cola long.")}</div>`);
                timer = setTimeout(poll, 2500);
            } else if (state.status === "Vista previa") {
                running = false;
                lockOptions(true);
                choiceHash = state.summary.file_hash || choiceHash;
                choiceFile = state.options?.source_file || choiceFile;
                const analyzed = state.options?.employer_assignments || {};
                if (JSON.stringify(analyzed) === JSON.stringify(choices)) choicesDirty = false;
                else if (!choicesDirty) choices = analyzed;
                saveChoices();
                renderSummary(state.summary);
                dialog.set_primary_action(__("Crear importaciones"), confirm);
                dialog.get_primary_btn().prop("disabled", choicesDirty || !state.summary.sections || !!state.summary.issues_count || !(state.summary.rows || state.summary.complementary_count || state.summary.deposit_count));
            } else if (state.status === "Completado") {
                running = false;
                let completed = `<div class="alert alert-success">${__("Carga completada. Revise las importaciones, partidas complementarias y depósitos vinculados. Los borradores no afectan saldos hasta confirmar su tratamiento.")}</div>`;
                if (state.progress) completed += `<p>${esc(state.progress)}</p>`;
                if (state.created_count > state.created.length) completed += `<p>${__("Se muestran los primeros 200 documentos. El resto está disponible en sus listas de importaciones, partidas y depósitos.")}</p>`;
                for (const unidentified of [false, true]) {
                    const records = state.created.filter(doc => (!doc.employer || doc.employer === "NO IDENTIFICADA") === unidentified);
                    if (!records.length) continue;
                    completed += `<section data-completed="${unidentified ? "unidentified" : "identified"}"><h4>${unidentified ? __("Casos no identificados") : __("Empresas identificadas")}</h4>` + table(
                    [__("Importación"), __("Empresa"), __("Fecha"), __("Filas"), __("Total US$")], records.map(doc => [
                        `<a href="/app/${doc.doctype === "CN Remittance Allocation" ? "cn-remittance-allocation" : doc.doctype === "CN Complementary Item" ? "cn-complementary-item" : "cn-accounting-import"}/${encodeURIComponent(doc.name)}">${esc(doc.name)}</a>`, unidentified
                            ? `<span tabindex="0" title="${attr(doc.description || __("Sin descripción del asiento"))}" style="cursor:help">${esc(doc.employer || __("Por identificar"))}</span>` : esc(doc.employer),
                        esc(frappe.datetime.str_to_user(doc.event_date)), esc(doc.rows), esc(format_currency(doc.total_usd, "USD")),
                    ])) + "</section>";
                }
                result.html(completed);
                dialog.set_primary_action(__("Cerrar"), () => dialog.hide());
                dialog.get_primary_btn().prop("disabled", false);
                if (onComplete) onComplete();
            } else {
                showError(state.error || __("No se pudo completar la carga."));
                if (state.progress) result.append(`<p>${esc(state.progress)}</p>`);
                if (state.resumable) {
                    lockOptions(true);
                    dialog.set_primary_action(__("Reanudar carga"), resume);
                }
            }
        } catch (error) {
            showError(__("No se pudo consultar la carga. El avance y las empresas analizadas permanecen guardados. Reintente la consulta."));
            dialog.set_primary_action(__("Consultar estado"), poll);
        }
    }
    dialog.set_secondary_action(() => {
        if (running) { frappe.msgprint(__("Espere a que termine la carga en curso.")); return; }
        token = null;
        currentPhase = "preview";
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
