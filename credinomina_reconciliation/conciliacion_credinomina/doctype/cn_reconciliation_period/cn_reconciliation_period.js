frappe.ui.form.on("CN Reconciliation Period", {
    setup(frm) {
        frm.set_query("existing_item", "provisional_adjustments", (doc, cdt, cdn) => {
            const row = locals[cdt][cdn];
            return {filters: {docstatus: 1, accounting_source_key: ["is", "set"],
                employer: frm.doc.employer, period: frm.doc.name,
                client_number: row.client_number, loan_number: row.loan_number,
                ...(row.category ? {category: row.category} : {})}};
        });
    },
    refresh(frm) {
        setPeriodEditing(frm);
        frm.set_df_property("application_basis", "read_only", frm.doc.status === "Cerrado" || !!frm.doc.prepared_deposit);
        setRemittanceDateEditing(frm);
        refreshPeriodExceptions(frm);
        refreshPeriodPending(frm);
        refreshApplicationQuality(frm);
        const proposals = frm.fields_dict?.provisional_adjustments?.grid;
        if (proposals) {
            proposals.df.cannot_add_rows = true;
            proposals.df.cannot_delete_rows = true;
        }
        if (frm.doc.reconciliation_mode !== "Historica") addTemplateButtons(frm);
        if (frm.is_new()) return;
        frm.add_custom_button(__("Ver cortes registrados"), () => showRegisteredCuts(frm), __("Más opciones"));

        if (frm.doc.reconciliation_mode !== "Historica") addExportButton(frm);
        if (frm.doc.status === "Cerrado") {
            if (frappe.session.user === "Administrator" || frappe.user.has_role("System Manager") || frappe.user.has_role("Supervisor Credinomina")) {
                frm.add_custom_button(__("Reabrir período"), () => showReopenDialog(frm));
            }
            return;
        }

        frm.add_custom_button(__("Registrar corte de control"), () => showControlCutDialog(frm));

        if (frm.doc.reconciliation_mode === "Historica") {
            frm.add_custom_button(__("Cerrar período histórico"), () => requestClose(frm));
            return;
        }

        if (!frm.doc.prepared_deposit) {
            frm.add_custom_button(__("Generar ajustes provisionales"), () => updateProvisionalAdjustments(frm, "generate_proposals"), __("Más opciones"));
            if ((frm.doc.provisional_adjustments || []).length) {
                frm.add_custom_button(__("Aprobar ajustes provisionales"), () => frappe.confirm(
                    __("¿Aprueba la clasificación de todas las diferencias? No se crearán partidas reales hasta trasladarlas a un depósito confirmado."),
                    () => updateProvisionalAdjustments(frm, "approve_proposals")
                ), __("Más opciones"));
            }
        }

        frm.add_custom_button(__("1. Cargar cobranza"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.import_collection",
                args: { period_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando cobranza y clientes..."),
            }).then(() => frm.reload_doc());
        });

        if ((frm.doc.collection_rows || []).length) frm.add_custom_button(__("2. Cargar deducción de empresa"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.import_employer_response",
                args: { period_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando y comparando deducciones..."),
            }).then(() => frm.reload_doc());
        });

        if (frm.doc.deduction_basis === "Depósito coincidente" && frm.doc.status !== "Cerrado") {
            frm.add_custom_button(__("Revertir reconocimiento por depósito"), () => {
                frappe.confirm(
                    __("Se retirará la deducción inferida y el depósito volverá a quedar disponible. ¿Continuar?"),
                    () => frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.revert_deposit_recognition",
                        args: { period_name: frm.doc.name },
                        freeze: true,
                    }).then(() => frm.reload_doc())
                );
            }, __("Más opciones"));
        } else if (
            frm.doc.status !== "Cerrado" && !frm.doc.deduction_basis &&
            !frm.doc.employer_response_file && (frm.doc.collection_rows || []).length
        ) {
            frm.add_custom_button(__("Reconocer cobranza como detalle de la empresa"), () => {
                showCollectionRecognition(frm);
            }, __("Más opciones"));
        }

        frm.add_custom_button(__("Cerrar período"), () => requestClose(frm));
    },
    collection_cycle(frm) {
        setRemittanceDateEditing(frm);
    },
    reconciliation_mode(frm) {
        setRemittanceDateEditing(frm);
    },
    historical_scope(frm) {
        if (frm.doc.historical_scope !== "Fecha exacta") {
            frm.set_value("historical_application_date", null);
        }
        if (frm.doc.historical_scope !== "Rango de fechas") {
            frm.set_value("historical_start_date", null);
            frm.set_value("historical_end_date", null);
        }
    },
    employer(frm) {
        if (!frm.is_new() || !frm.doc.employer || frm.doc.reconciliation_mode === "Historica") return;
        frappe.db.get_value("CN Employer", frm.doc.employer, "payroll_frequency").then((result) => {
            if (result.message?.payroll_frequency === "Mensual") {
                frm.set_value("collection_cycle", "Mensual");
            } else if (result.message?.payroll_frequency === "Quincenal" && frm.doc.collection_cycle === "Mensual") {
                frm.set_value("collection_cycle", "");
            }
        });
    },
});

async function updateProvisionalAdjustments(frm, action) {
    if (frm.is_dirty()) await frm.save();
    await frappe.call({
        method: `credinomina_reconciliation.provisional_adjustments.${action}`,
        args: {period_name: frm.doc.name}, freeze: true,
        freeze_message: __("Revisando ajustes provisionales…"),
    });
    await frm.reload_doc();
    frappe.show_alert({message: __("Tabla de ajustes actualizada. Los saldos no han cambiado."), indicator: "green"});
}

function refreshApplicationQuality(frm) {
    const wrapper = frm.fields_dict?.quality_html?.$wrapper;
    if (!wrapper) return;
    if (frm.doc.reconciliation_mode === "Historica") { wrapper.empty(); return; }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const rows = frm.doc.collection_rows || [];
    const money = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    wrapper.html(`<h4>${esc(__("Control de calidad de la aplicación"))}</h4>
        <p>${esc(__("Compara la aplicación únicamente contra la base elegida para este período: {0}. Ambos detalles se conservan; el no elegido no interviene en este control. Conforme no significa depositado. Guarde y actualice desde Conciliar esta empresa en la importación contable.", [frm.doc.application_basis || __("Sin elegir")]))}</p>
        <div class="table-responsive"><table class="table table-bordered">
        <thead><tr>${["Cliente", "Crédito", "Comparado contra", "A aplicar US$", "Aplicado US$", "Diferencia US$", "Control de aplicación", "Evidencia de deducción"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(row => `<tr>
            <td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td>
            <td>${esc(row.quality_basis || "—")}</td>
            <td class="text-right">${money(row.quality_status === "Revisar base de comparación" || !row.quality_status ? null : row.quality_expected_usd)}</td>
            <td class="text-right">${money(row.applied_usd)}</td>
            <td class="text-right">${money(row.quality_status === "Revisar base de comparación" || !row.quality_status ? null : row.quality_difference_usd)}</td>
            <td><span class="indicator-pill ${String(row.quality_status || "").startsWith("Conforme") ? "green" : "orange"}">${esc(__(row.quality_status || "Pendiente de actualizar"))}</span></td>
            <td>${esc(__(row.deduction_status || "Pendiente de detalle"))}</td>
        </tr>`).join("")}</tbody></table></div>`);
}

async function refreshPeriodPending(frm, start = 0, search = "", kind = "") {
    const wrapper = frm.fields_dict?.pending_html?.$wrapper;
    if (!wrapper) return;
    const request = {};
    frm._cn_pending_request = request;
    wrapper.off(".cnPending");
    if (frm.is_new()) { wrapper.empty(); return; }
    const period = frm.doc.name;
    const current = () => frm._cn_pending_request === request && frm.doc.name === period;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.html(`<p>${esc(__("Consultando pendientes detectados…"))}</p>`);
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.period_pending.get_period_pending",
            args: {period_name: period, start, search, kind},
        });
        if (!current()) return;
        const data = response.message;
        if (!Array.isArray(data?.rows)) throw new Error("missing_pending_rows");
        const money = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
        const routes = {"CN Accounting Import": "cn-accounting-import", "CN Reconciliation Period": "cn-reconciliation-period", "CN Remittance Allocation": "cn-remittance-allocation"};
        wrapper.html(`<p class="text-muted">${esc(__("Diferencias detectadas en la última conciliación guardada. No son necesariamente excepciones registradas; consultar no modifica datos ni concilia."))}</p>
            <div class="row mb-3">
                <div class="col-sm-6"><label>${esc(__("Buscar"))}</label><input class="form-control" data-pending="search" value="${esc(search)}" placeholder="${esc(__("Cliente, crédito, documento, fila o motivo"))}"></div>
                <div class="col-sm-4"><label>${esc(__("Tipo"))}</label><select class="form-control" data-pending="kind">${["", "Aplicación", "Cobranza", "Depósito"].map(value => `<option value="${esc(value)}" ${kind === value ? "selected" : ""}>${esc(__(value || "Todos"))}</option>`).join("")}</select></div>
                <div class="col-sm-2 d-flex align-items-end"><button type="button" class="btn btn-default" data-pending="filter">${esc(__("Consultar"))}</button></div>
            </div>
            ${data.restricted?.length ? `<p class="text-warning">${esc(__("Vista parcial: no tiene acceso a algunos movimientos o depósitos relacionados."))}</p>` : ""}
            <p role="status">${esc(__("Pendientes visibles: {0} · Con los filtros: {1}", [data.total, data.count]))}</p>
            ${data.rows.length ? `<div class="table-responsive"><table class="table table-bordered" style="font-size:14px">
                <thead><tr>${["Tipo / documento", "Fila", "Cliente / crédito", "Aplicado neto US$", "Depositado US$", "Pendiente US$", "Estado / motivo"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
                <tbody>${data.rows.map(row => `<tr>
                    <td>${esc(__(row.kind))}<br><a href="/app/${routes[row.doctype] || "cn-reconciliation-period"}/${encodeURIComponent(row.source)}">${esc(row.source)}</a></td>
                    <td>${esc(row.row ?? "—")}</td>
                    <td>${esc(row.client_name || "—")}<div class="text-muted">${esc(row.client_number)} ${esc(row.loan_number)}</div></td>
                    <td class="text-right text-nowrap">${money(row.applied)}</td><td class="text-right text-nowrap">${money(row.paid)}</td><td class="text-right text-nowrap">${money(row.pending)}</td>
                    <td>${esc(__(row.status))}<details><summary>${esc(__("Ver motivo"))}</summary>${esc(row.reason)}</details></td>
                </tr>`).join("")}</tbody></table></div>` : `<p>${esc(__(data.total ? "No hay pendientes que coincidan con los filtros." : "No hay pendientes detectados en los registros visibles vinculados a este período."))}</p>`}
            <p class="text-muted">${esc(__("En depósitos se muestra el importe completo y su saldo sin distribuir, que puede corresponder a otros períodos. No se suma a lo pendiente de aplicaciones o cobranzas."))}</p>
            <div class="d-flex justify-content-between"><button type="button" class="btn btn-default btn-sm" data-pending="previous" ${start ? "" : "disabled"}>${esc(__("Anterior"))}</button>
                <span>${data.rows.length ? esc(__("Mostrando {0}–{1} de {2}", [start + 1, start + data.rows.length, data.count])) : ""}</span>
                <button type="button" class="btn btn-default btn-sm" data-pending="next" ${start + data.rows.length < data.count ? "" : "disabled"}>${esc(__("Siguiente"))}</button></div>`);
        const filter = () => refreshPeriodPending(frm, 0, wrapper.find('[data-pending="search"]').val(), wrapper.find('[data-pending="kind"]').val());
        wrapper.on("click.cnPending", '[data-pending="filter"]', filter);
        wrapper.on("change.cnPending", '[data-pending="kind"]', filter);
        wrapper.on("keydown.cnPending", '[data-pending="search"]', event => { if (event.key === "Enter") { event.preventDefault(); filter(); } });
        wrapper.on("click.cnPending", '[data-pending="previous"]', () => refreshPeriodPending(frm, Math.max(start - 50, 0), search, kind));
        wrapper.on("click.cnPending", '[data-pending="next"]', () => refreshPeriodPending(frm, start + 50, search, kind));
    } catch (error) {
        if (!current()) return;
        wrapper.html(`<p class="text-danger">${esc(__("No se pudieron consultar los pendientes. Compruebe sus permisos o vuelva a intentar."))}</p><button type="button" class="btn btn-default btn-sm" data-pending="retry">${esc(__("Reintentar"))}</button>`);
        wrapper.on("click.cnPending", '[data-pending="retry"]', () => refreshPeriodPending(frm, start, search, kind));
    }
}

async function refreshPeriodExceptions(frm, offset = 0, includeClosed = false) {
    const wrapper = frm.fields_dict?.exceptions_html?.$wrapper;
    if (!wrapper) return;
    const request = {};
    frm._cn_exception_request = request;
    const period = frm.doc.name;
    wrapper.off(".cnExceptions");
    if (frm.is_new()) { wrapper.empty(); return; }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.html(`<p class="text-muted">${esc(__("Cargando excepciones…"))}</p>`);
    const isCurrent = () => frm._cn_exception_request === request && frm.doc.name === period;
    const filters = {period};
    if (!includeClosed) filters.status = ["in", ["Abierta", "En revision"]];
    try {
        // Standard get_list enforces the exception DocType's read permissions
        // and user restrictions; never fetch cases with an unrestricted API.
        const response = await frappe.call({
            method: "frappe.client.get_list",
            args: {doctype: "CN Reconciliation Exception", filters,
                fields: ["name", "exception_type", "client_name", "client_number", "loan_number",
                    "amount_usd", "status", "assigned_to", "commitment_date"],
                order_by: "status asc, modified desc, name asc", limit_start: offset, limit_page_length: 21},
        });
        if (!isCurrent()) return;
        const rows = response.message;
        if (!Array.isArray(rows)) throw new Error("missing_exception_list");
        const visible = rows.slice(0, 20);
        const colors = {Abierta: "red", "En revision": "orange", Resuelta: "green", Descartada: "gray"};
        const body = visible.map(row => `<tr>
            <td><a href="/app/cn-reconciliation-exception/${encodeURIComponent(row.name)}">${esc(row.name)}</a>
                <div>${esc(row.exception_type)}</div></td>
            <td>${esc(row.client_name || __("Sin cliente identificado"))}
                <div class="text-muted">${esc(__("Cliente: {0} · Crédito: {1}", [row.client_number || "—", row.loan_number || "—"]))}</div></td>
            <td class="text-right text-nowrap">${esc(format_currency(row.amount_usd || 0, "USD", 2))}</td>
            <td><span class="indicator-pill ${colors[row.status] || "gray"}">${esc(row.status === "En revision" ? __("En revisión") : __(row.status))}</span></td>
            <td>${esc(row.assigned_to || __("Sin asignar"))}
                <div class="text-muted">${row.commitment_date ? esc(frappe.datetime.str_to_user(row.commitment_date)) : esc(__("Sin fecha compromiso"))}</div></td>
        </tr>`).join("");
        wrapper.html(`<div class="d-flex flex-wrap align-items-center justify-content-between mb-3" style="gap: 12px">
            <label class="mb-0"><input type="checkbox" data-action="closed" ${includeClosed ? "checked" : ""}> ${esc(__("Incluir resueltas y descartadas"))}</label>
            <div><button type="button" class="btn btn-default btn-sm" data-action="refresh">${esc(__("Actualizar"))}</button>
                <button type="button" class="btn btn-default btn-sm" data-action="list">${esc(__("Ver listado"))}</button></div></div>
            ${visible.length ? `<div class="table-responsive"><table class="table table-bordered" style="font-size: 14px">
                <thead><tr>${["Excepción", "Cliente / crédito", "Monto US$", "Estado", "Responsable / compromiso"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
                <tbody>${body}</tbody></table></div>` : `<p class="text-muted">${esc(__(includeClosed ? "No hay excepciones registradas para este período." : "No hay excepciones abiertas para este período. Puede incluir las resueltas y descartadas."))}</p>`}
            <div class="d-flex justify-content-between align-items-center">
                <span class="text-muted">${visible.length ? esc(__("Mostrando {0}–{1}", [offset + 1, offset + visible.length])) : ""}</span>
                <div>${offset ? `<button type="button" class="btn btn-default btn-sm" data-action="previous">${esc(__("Anterior"))}</button>` : ""}
                ${rows.length > 20 ? `<button type="button" class="btn btn-default btn-sm" data-action="next">${esc(__("Siguiente"))}</button>` : ""}</div></div>`);
        wrapper.on("change.cnExceptions", '[data-action="closed"]', event => refreshPeriodExceptions(frm, 0, event.currentTarget.checked));
        wrapper.on("click.cnExceptions", '[data-action="refresh"]', () => refreshPeriodExceptions(frm, 0, includeClosed));
        wrapper.on("click.cnExceptions", '[data-action="previous"]', () => refreshPeriodExceptions(frm, Math.max(0, offset - 20), includeClosed));
        wrapper.on("click.cnExceptions", '[data-action="next"]', () => refreshPeriodExceptions(frm, offset + 20, includeClosed));
        wrapper.on("click.cnExceptions", '[data-action="list"]', () => frappe.set_route("List", "CN Reconciliation Exception", {period}));
    } catch (error) {
        if (!isCurrent()) return;
        wrapper.html(`<p class="text-danger">${esc(__("No se pudieron consultar las excepciones. Compruebe sus permisos o vuelva a intentar."))}</p>
            <button type="button" class="btn btn-default btn-sm" data-action="retry">${esc(__("Reintentar"))}</button>`);
        wrapper.on("click.cnExceptions", '[data-action="retry"]', () => refreshPeriodExceptions(frm, offset, includeClosed));
    }
}

function setPeriodEditing(frm) {
    const closed = frm.doc.status === "Cerrado";
    if (closed) {
        // Keep field-level locks: changing form permissions alone can leave
        // controls editable after Frappe refreshes permissions or dependencies.
        if (!frm._cn_period_read_only) {
            frm._cn_period_read_only = new Map(
                frm.fields.map(field => [field.df.fieldname, field.df.read_only || 0])
            );
        }
        for (const field of frm.fields) {
            frm.set_df_property(field.df.fieldname, "read_only", 1);
        }
        frm.disable_save();
        const result = frappe.utils.escape_html(frm.doc.status_before_close || __("Sin resultado registrado"));
        frm.set_intro(__("Período cerrado · Resultado al cerrar: {0}. Solo consulta; use Reabrir período antes de modificarlo.", [result]), "blue");
    } else if (frm._cn_period_read_only) {
        // The same Form instance is reused when reopening or navigating to
        // another period. Restore only the properties changed by this lock.
        for (const [fieldname, readOnly] of frm._cn_period_read_only) {
            frm.set_df_property(fieldname, "read_only", readOnly);
        }
        delete frm._cn_period_read_only;
        if (frm.get_perm(0, "write")) frm.enable_save();
        frm.set_intro("");
    }
}

async function requestClose(frm) {
    if (frm.closure_running) return;
    frm.closure_running = true;
    let name;
    const progressId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const event = "cn_period_closure_progress";
    const onProgress = data => {
        if (data.period_name !== name || data.progress_id !== progressId) return;
        $("#freeze .freeze-message").text(`${__("Cerrando período")}: ${data.percent}% — ${__(data.message)}`);
    };
    try {
        if (frm.is_dirty()) await frm.save();
        name = frm.doc.name; // Saving a changed company/month can rename the period.
        frappe.realtime.on(event, onProgress);
        await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.close_period",
            args: { period_name: name, progress_id: progressId },
            freeze: true,
            freeze_message: __("Revisando la empresa y los registros relacionados con este período…"),
        });
        await frm.reload_doc();
        frappe.show_alert({message: __("Período cerrado. El resultado de conciliación quedó conservado."), indicator: "green"});
    } finally {
        frappe.realtime.off(event, onProgress);
        frm.closure_running = false;
    }
}

function showControlCutDialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Registrar corte de control"),
        fields: [
            {
                fieldname: "notice", fieldtype: "HTML",
                options: `<p>${__("Este corte conserva archivos privados Excel y JSON con los importes, detalle y antigüedad actuales de este período. No cierra el período ni reconstruye saldos a una fecha anterior. Los cortes anteriores se conservan aunque lleguen datos posteriores.")}</p>`,
            },
            {
                fieldname: "note", fieldtype: "Small Text",
                label: __("Motivo y próxima gestión de los pendientes"), reqd: 1,
            },
        ],
        primary_action_label: __("Registrar corte"),
        async primary_action(values) {
            if (frm.is_dirty()) await frm.save();
            await frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.record_control_cut",
                args: { period_name: frm.doc.name, note: values.note },
                freeze: true,
            });
            dialog.hide();
            frm.reload_doc();
        },
    });
    dialog.show();
}

async function showRegisteredCuts(frm) {
    const dialog = new frappe.ui.Dialog({title: __("Cortes registrados del período"), size: "extra-large", fields: [{fieldname: "cuts", fieldtype: "HTML"}]});
    const wrapper = dialog.get_field("cuts").$wrapper;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    let start = 0;
    wrapper.html(`<p>${esc(__("Cada corte conserva los valores del momento en que fue registrado; no recalcula el pasado. Incluya los archivos privados y el historial Version en sus respaldos. Los cortes anteriores a esta mejora no tienen archivo detallado."))}</p>`);
    async function load() {
        try {
            const {message} = await frappe.call({method: "credinomina_reconciliation.control_cuts.get_cuts", args: {period_name: frm.doc.name, start}});
            wrapper.find("[data-more-cuts]").remove();
            if (!message.rows?.length && !start) wrapper.append(`<p>${esc(__("No hay cortes detallados registrados."))}</p>`);
            for (const cut of message.rows || []) {
                const links = ["xlsx", "json"].map(ext => {
                    const file = cut.files?.[ext];
                    return file?.url?.startsWith("/private/files/") ? `<a class="btn btn-default btn-sm" href="${esc(file.url)}" target="_blank" rel="noopener">${esc(__("Descargar"))} ${ext.toUpperCase()}</a>` : "";
                }).join(" ");
                wrapper.append(`<section class="border rounded p-3 mb-3" style="font-size:14px"><h5>${esc(frappe.datetime.str_to_user(cut.recorded_at))} · ${esc(cut.recorded_by)}</h5><p>${esc(cut.summary)}</p><p>${esc(cut.note)}</p><p class="text-muted">${esc(cut.scope)}</p>${links}</section>`);
            }
            start = message.next_start;
            if (message.has_more) wrapper.append(`<button class="btn btn-default" data-more-cuts>${esc(__("Ver más cortes"))}</button>`);
        } catch (_) {
            frappe.msgprint(__("No se pudieron consultar los cortes. Intente nuevamente."));
        }
    }
    wrapper.on("click", "[data-more-cuts]", () => load());
    dialog.show();
    await load();
}

function addExportButton(frm) {
    frm.add_custom_button(__("Exportar archivo empresa"), () => {
        frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.export_collection",
            args: { period_name: frm.doc.name },
        }).then((response) => {
            if (response.message?.file_url) window.open(response.message.file_url);
        });
    }, __("Más opciones"));
}

function addTemplateButtons(frm) {
    frm.add_custom_button(__("Plantilla de cobranza"), () => {
        downloadTemplate("cobranza");
    }, __("Plantillas"));
    frm.add_custom_button(__("Plantilla de detalle empresa"), () => {
        downloadTemplate("empresa", frm.is_new() ? "" : frm.doc.name);
    }, __("Plantillas"));
}

function downloadTemplate(templateType, periodName) {
    const params = new URLSearchParams({ template_type: templateType });
    if (periodName) params.set("period_name", periodName);
    window.open(
        `/api/method/credinomina_reconciliation.template_download.download_import_template?${params}`,
        "_blank"
    );
}

function showReopenDialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Reabrir período"),
        fields: [
            {
                fieldname: "reason", fieldtype: "Small Text",
                label: __("Motivo de la reapertura"), reqd: 1,
            },
        ],
        primary_action_label: __("Reabrir período"),
        primary_action(values) {
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.reopen_period",
                args: { period_name: frm.doc.name, reason: values.reason },
                freeze: true,
            }).then(() => {
                dialog.hide();
                frm.reload_doc();
            });
        },
    });
    dialog.show();
}

function setRemittanceDateEditing(frm) {
    frm.set_df_property(
        "remittance_due_date", "read_only",
        frm.doc.status === "Cerrado" || frm.doc.reconciliation_mode === "Historica" ||
        !["Primera quincena", "Segunda quincena"].includes(frm.doc.collection_cycle)
    );
}

function showCollectionRecognition(frm) {
    let saving = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Reconocer cobranza como detalle de la empresa"),
        fields: [
            {fieldname: "warning", fieldtype: "HTML", options: `<p>${__("Se copiarán los importes de la cobranza a Deducido US$ y Deducido C$. Use esta opción solo si la empresa confirmó la deducción completa. No registra un depósito ni confirma su recepción.")}</p>`},
            {fieldname: "evidence_date", fieldtype: "Date", label: __("Fecha de evidencia de deducción"), reqd: 1, default: frm.doc.deduction_evidence_date},
            {fieldname: "confirmed", fieldtype: "Check", label: __("Confirmo que la empresa dedujo la cobranza completa"), reqd: 1},
        ],
        primary_action_label: __("Reconocer cobranza"),
        async primary_action(values) {
            if (saving || !values.confirmed) return;
            saving = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                if (frm.is_dirty()) await frm.save();
                const response = await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.recognize_collection_as_employer_detail",
                    args: {period_name: frm.doc.name, evidence_date: values.evidence_date, confirmed: values.confirmed},
                    freeze: true, freeze_message: __("Reconociendo detalle de la empresa…"),
                });
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __("Cobranza reconocida como detalle de la empresa: {0} filas.", [response.message.rows]), indicator: "green"});
            } finally {
                saving = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
}
