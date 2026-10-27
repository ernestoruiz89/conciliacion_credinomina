function update_currency_fields(frm) {
    const is_nio = frm.doc.currency === "NIO";
    frm.toggle_reqd("currency", frm.is_new() || frm.doc.status === "Borrador");
    frm.toggle_display("manual_fx_rate", is_nio);
    frm.toggle_reqd("manual_fx_rate", is_nio);
}

frappe.ui.form.on("CN Accounting Import", {
    refresh(frm) {
        update_currency_fields(frm);
        frm.set_query("employer", () => ({filters: {active: 1}}));
        frm.set_query("historical_period", () => ({
            filters: { employer: frm.doc.employer, status: ["!=", "Cerrado"] },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { employer: frm.doc.employer, status: ["!=", "Cerrado"] },
        }));
        frm.set_query("portfolio_snapshot", () => ({
            query: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.get_company_portfolio_snapshots",
            filters: {employer: frm.doc.employer},
        }));
        updateImportExceptionNotice(frm);
        loadAccountingClientSummary(frm);
        showAccountingRecovery(frm);
        if (frm.is_new()) return;

        if (frappe.model.can_create("CN Reconciliation Period")) {
            frm.add_custom_button(__("Crear período"), () => createAccountingPeriod(frm));
        }

        frm.add_custom_button(__("Cargar movimientos contables"), () => loadAccountingMovements(frm));
        frm.add_custom_button(__("Saldos por cliente"), () => showAccountingClientSummary(frm), __("Consultas"));
        frm.add_custom_button(__("Revisar pendientes"), () => showAccountingImportIssues(frm), __("Consultas"));

        if (frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Conciliar período predeterminado"), () => reconcileCompany(frm));
        }
    },

    currency(frm) {
        if (frm.doc.currency !== "NIO") {
            frm.set_value("manual_fx_rate", 0);
        }
        update_currency_fields(frm);
    },

    employer(frm) {
        frm.set_value("portfolio_snapshot", "");
        frm.set_value("historical_period", "");
    },
});

function loadAccountingMovements(frm) {
    const period = frm.doc.historical_period;
    const differing = (frm.doc.rows || []).filter(row => row.event_type === "Aplicacion"
        && row.historical_period && row.historical_period !== period);
    if (!period || !differing.length) return importAccountingMovements(frm, false);
    const dialog = new frappe.ui.Dialog({
        title: __("Período de las aplicaciones"),
        fields: [{fieldname: "apply_default_period", fieldtype: "Check", default: 1,
            label: __("Aplicar {0} a todas las aplicaciones", [period]),
            description: __("Hay {0} aplicaciones asignadas a otro período. Desmarque para conservar los períodos por fila. Se mantienen los ajustes vinculados; las filas con depósitos asignados o períodos cerrados requieren resolver esos vínculos antes de cambiarlas.", [differing.length])}],
        primary_action_label: __("Cargar movimientos"),
        primary_action(values) {
            dialog.hide();
            return importAccountingMovements(frm, Boolean(Number(values.apply_default_period)));
        },
    });
    dialog.show();
}

async function importAccountingMovements(frm, applyDefaultPeriod) {
    if (frm.is_dirty()) await frm.save();
    const response = await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.import_source_file",
        args: {import_name: frm.doc.name, apply_default_period: Number(applyDefaultPeriod)},
        freeze: true,
        freeze_message: __("Cargando aplicaciones y actualizando saldos sin redistribuir depósitos..."),
    });
    const result = response.message || {};
    const reassigned = result.reassigned_rows
        ? __(" Se actualizó el período de {0} aplicaciones a {1}.", [result.reassigned_rows, result.default_period]) : "";
    frappe.show_alert({
        message: __("Movimientos cargados. Se conservaron las distribuciones de depósitos. Para cambiarlas, use Conciliar en el depósito.") + reassigned,
        indicator: "green",
    }, 8);
    if (result.import_name && result.import_name !== frm.doc.name) {
        return frappe.set_route("Form", frm.doctype, result.import_name);
    }
    return frm.reload_doc();
}

function showAccountingRecovery(frm) {
    const wrapper = frm.fields_dict?.recovery_html?.$wrapper;
    if (!wrapper) return;
    wrapper.off(".cnRecovery");
    if (frm.is_new() || !frm.doc.file_hash || !(frm.doc.rows || []).length) {
        wrapper.empty();
        return;
    }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.html(`<div class="mt-3"><button type="button" class="btn btn-default btn-sm cn-download-recovery">${esc(__("Generar CSV de recuperación"))}</button>
        <p class="text-muted mt-2">${esc(__("Se reconstruye con las filas contables guardadas. No es una copia exacta del archivo original; revise el CSV antes de volver a cargarlo."))}</p></div>`);
    wrapper.on("click.cnRecovery", ".cn-download-recovery", () => {
        const method = "credinomina_reconciliation.accounting_recovery.download_recovery_csv";
        window.open(`/api/method/${method}?import_name=${encodeURIComponent(frm.doc.name)}`, "_blank");
    });
}

function accountingClientSummaryTable(rows, page = 0) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const fields = ["applied_usd", "assigned_usd", "rounding_usd", "balance_usd"];
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const total = field => rows.some(row => row[field] == null) ? null
        : rows.reduce((sum, row) => sum + Math.round(Number(row[field]) * 100), 0) / 100;
    const body = rows.slice(page * 50, (page + 1) * 50).map(row => {
        const accent = row.status === "Conciliado" ? "var(--green-500, #2f9e44)" : row.status === "Revisar" ? "var(--orange-500, #cc6b00)" : "var(--border-color, #d1d5db)";
        return `<tr><td style="border-left:3px solid ${accent};color:var(--text-color)"><strong>${esc(row.client_name)}</strong>
            <div class="text-muted">${esc((row.loans || []).join(", "))}</div>
            ${(row.observations || []).map(note => `<div class="text-warning">${esc(note)}</div>`).join("")}</td>
            <td>${esc(row.client_number || "—")}</td>
            ${fields.map(field => `<td class="text-right" style="white-space:nowrap">${amount(row[field])}</td>`).join("")}
            <td><span style="display:inline-block;padding:2px 8px;border:1px solid ${accent};border-radius:999px;color:var(--text-color);background-color:var(--bg-color)">${esc(__(row.status))}</span></td>
            <td>${row.status !== "Conciliado" && row.source_row_name
                ? `<button type="button" class="btn btn-default btn-xs cn-client-related" data-source-row="${esc(row.source_row_name)}">${esc(__("Ver registros relacionados"))}</button>`
                : ""}</td></tr>`;
    }).join("");
    return `<div class="table-responsive"><table class="table table-bordered table-hover" style="font-size:var(--text-base, 14px);color:var(--text-color);background-color:var(--bg-color);--bs-table-color:var(--text-color);--bs-table-bg:var(--bg-color);--bs-table-border-color:var(--border-color)">
        <thead><tr>${["Cliente / créditos", "Nro. Cliente", "Aplicado neto US$", "Asignado a depósitos US$", "Ajuste de centavos US$", "Saldo US$", "Estado", "Registros relacionados"].map((label, i) => `<th class="${i >= 2 && i <= 5 ? "text-right" : ""}">${esc(__(label))}</th>`).join("")}</tr></thead>
        <tbody>${body || `<tr><td colspan="8">${esc(__("No hay clientes que coincidan."))}</td></tr>`}</tbody>
        <tfoot><tr><th colspan="2">${esc(__("Total de clientes filtrados"))} (${rows.length})</th>
        ${fields.map(field => `<th class="text-right" style="white-space:nowrap">${amount(total(field))}</th>`).join("")}<th></th><th></th></tr></tfoot>
    </table></div>`;
}

async function showAccountingRelatedRecords(frm, sourceRowName, button) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Guarde la importación antes de consultar los registros relacionados."));
        return;
    }
    button.prop("disabled", true);
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.accounting_related_records.get_related_pending",
            args: {import_name: frm.doc.name, source_row_name: sourceRowName},
        });
        await frappe.require("/assets/credinomina_reconciliation/js/related_pending_modal.js");
        window.credinominaShowRelatedPending(response.message || {});
    } catch (error) {
        frappe.msgprint(__("No se pudieron consultar los registros relacionados. Revise sus permisos o vuelva a intentar."));
    } finally {
        button.prop("disabled", false);
    }
}

function accountingBalanceSummary(totals) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => value == null ? "—" : format_currency(value, "USD", 2);
    const fields = [
        [__("Importe US$"), totals?.amount_usd],
        [__("Depósitos US$"), totals?.deposits_usd],
        [__("Partidas complementarias US$"), totals?.complementary_usd],
        [__("Saldo US$"), totals?.balance_usd],
    ];
    return `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:12px">
        ${fields.map(([label, value]) => `<div style="padding:12px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg)">
            <div class="small text-muted">${esc(label)}</div><div style="font-size:16px;font-weight:600;margin-top:4px">${esc(amount(value))}</div>
        </div>`).join("")}
    </div><p class="text-muted" style="margin:12px 0 0">${esc(__("Solo aplicaciones efectivas. Depósitos y partidas reflejan la cobertura atribuible a esta importación; el saldo suma pendientes sin compensar sobrantes entre créditos. «—» indica que una distribución compartida no permite determinar el importe sin duplicarlo."))}</p>`;
}

function showAccountingClientSummary(frm) {
    if (frm.is_new() || frm.is_dirty()) {
        frappe.msgprint(__("Guarde la importación antes de consultar los saldos por cliente."));
        return;
    }
    const dialog = new frappe.ui.Dialog({title: __("Saldos por cliente"), size: "extra-large",
        fields: [{fieldname: "client_summary", fieldtype: "HTML"}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide()});
    dialog.show();
    return loadAccountingClientSummary(frm, dialog.get_field("client_summary").$wrapper);
}

async function loadAccountingClientSummary(frm, wrapper = null) {
    const balanceWrapper = frm.fields_dict?.balance_summary_html?.$wrapper;
    if (!wrapper && !balanceWrapper) return;
    const showBalance = html => { if (balanceWrapper) balanceWrapper.html(html); };
    const showClients = html => { if (wrapper) wrapper.html(html); };
    const request = frm._client_summary_request = (frm._client_summary_request || 0) + 1;
    const name = frm.doc.name;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper?.off(".cnClientSummary");
    balanceWrapper?.off(".cnAccountingBalance");
    if (frm.is_new() || frm.is_dirty()) {
        showClients(`<p class="text-muted">${esc(__("Guarde el documento para consultar los saldos por cliente."))}</p>`);
        showBalance(`<p class="text-muted">${esc(__("Guarde el documento para consultar los saldos."))}</p>`);
        return;
    }
    showClients(`<p class="text-muted">${esc(__("Consultando saldos por cliente..."))}</p>`);
    showBalance(`<p class="text-muted">${esc(__("Consultando saldos..."))}</p>`);
    const current = () => frm._client_summary_request === request && frm.doc.name === name;
    try {
        const response = await frappe.call({method: "credinomina_reconciliation.accounting_client_summary.get_client_summary", args: {import_name: name}});
        if (!current()) return;
        if (frm.is_dirty()) {
            showClients(`<p class="text-muted">${esc(__("Hay cambios sin guardar. Guarde para actualizar los saldos."))}</p>`);
            showBalance(`<p class="text-muted">${esc(__("Hay cambios sin guardar. Guarde para actualizar los saldos."))}</p>`);
            return;
        }
        const rows = response.message.rows || [];
        showBalance(accountingBalanceSummary(response.message.totals));
        if (!wrapper) return;
        const filters = frm._client_summary_filters?.name === name
            ? frm._client_summary_filters : {name, query: "", onlyPending: false};
        frm._client_summary_filters = filters;
        wrapper.html(`<p class="text-muted">${esc(__("Solo aplicaciones efectivas de esta importación. El aplicado neto ya descuenta los ajustes confirmados; no incluye cobranza ni saldos a favor sin vincular. Datos guardados, según la última conciliación."))}</p>
            <div class="flex mb-3" style="gap:12px;align-items:center;flex-wrap:wrap">
                <input class="form-control input-sm cn-client-search" style="max-width:360px" aria-label="${esc(__("Buscar cliente, número o crédito"))}" placeholder="${esc(__("Buscar cliente, número o crédito"))}" value="${esc(filters.query)}">
                <label class="checkbox-inline" style="white-space:nowrap"><input type="checkbox" class="cn-client-pending" ${filters.onlyPending ? "checked" : ""}> ${esc(__("Solo pendientes"))}</label>
                <button type="button" class="btn btn-default btn-sm cn-client-refresh">${esc(__("Actualizar resumen"))}</button>
            </div><div class="cn-client-table"></div>
            <div class="flex" style="gap:12px;align-items:center"><button type="button" class="btn btn-default btn-sm cn-client-prev">${esc(__("Anterior"))}</button>
                <span class="cn-client-page"></span><button type="button" class="btn btn-default btn-sm cn-client-next">${esc(__("Siguiente"))}</button></div>
            <p class="text-muted mt-2">${esc(__("Saldo = aplicado neto + ajuste de centavos − asignado. No se compensan saldos entre créditos. «—» indica que falta determinar un importe; el total tampoco se presenta como completo."))}</p>`);
        let page = 0;
        const render = () => {
            const filtered = rows.filter(row => (!filters.onlyPending || row.status !== "Conciliado")
                && [row.client_name, row.client_number, ...(row.loans || [])].join(" ").toLocaleLowerCase().includes(filters.query));
            const pages = Math.max(1, Math.ceil(filtered.length / 50));
            page = Math.max(0, Math.min(page, pages - 1));
            wrapper.find(".cn-client-table").html(accountingClientSummaryTable(filtered, page));
            wrapper.find(".cn-client-page").text(__("Página {0} de {1}", [page + 1, pages]));
            wrapper.find(".cn-client-prev").prop("disabled", page === 0);
            wrapper.find(".cn-client-next").prop("disabled", page === pages - 1);
        };
        wrapper.on("input.cnClientSummary", ".cn-client-search", event => { filters.query = event.target.value.trim().toLocaleLowerCase(); page = 0; render(); });
        wrapper.on("change.cnClientSummary", ".cn-client-pending", event => { filters.onlyPending = event.target.checked; page = 0; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-prev", () => { page--; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-next", () => { page++; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-refresh", () => loadAccountingClientSummary(frm, wrapper));
        wrapper.on("click.cnClientSummary", ".cn-client-related", function () {
            showAccountingRelatedRecords(frm, this.dataset.sourceRow, $(this));
        });
        render();
    } catch (error) {
        if (!current()) return;
        const retry = `<button type="button" class="btn btn-default btn-sm cn-client-retry">${esc(__("Reintentar"))}</button>`;
        showClients(`<p class="text-danger">${esc(__("No se pudo consultar el resumen. Revise sus permisos o vuelva a intentar."))}</p>${retry}`);
        showBalance(`<p class="text-danger">${esc(__("No se pudieron consultar los saldos. Revise sus permisos o vuelva a intentar."))}</p>${retry}`);
        wrapper?.on("click.cnClientSummary", ".cn-client-retry", () => loadAccountingClientSummary(frm, wrapper));
        balanceWrapper?.on("click.cnAccountingBalance", ".cn-client-retry", () => loadAccountingClientSummary(frm, wrapper));
    }
}

async function createAccountingPeriod(frm) {
    if (frm._creating_period) return;
    frm._creating_period = true;
    try {
        if (frm.is_dirty()) await frm.save();
        const response = await frappe.call({
            method: "credinomina_reconciliation.accounting_period.get_period_defaults",
            args: {import_name: frm.doc.name}, freeze: true,
            freeze_message: __("Preparando período..."),
        });
        const defaults = response.message;
        let dialog;
        const historical = () => {
            const month = String(dialog.get_value("payroll_month") || "").slice(0, 7);
            return month >= "2025-04" && month <= "2026-08";
        };
        const updateFields = () => {
            if (!dialog) return;
            const isHistorical = historical();
            const scope = dialog.get_value("historical_scope");
            dialog.set_df_property("historical_scope", "hidden", !isHistorical);
            for (const field of ["historical_application_date", "historical_start_date", "historical_end_date"]) {
                const visible = isHistorical && scope === (field === "historical_application_date" ? "Fecha exacta" : "Rango de fechas");
                dialog.set_df_property(field, "hidden", !visible);
                dialog.set_df_property(field, "reqd", visible);
            }
            dialog.set_df_property("collection_cycle", "hidden", isHistorical);
            dialog.set_df_property("collection_cycle", "reqd", !isHistorical);
            const exactDate = !isHistorical && dialog.get_value("collection_cycle") === "Fecha exacta";
            dialog.set_df_property("cutoff_date", "hidden", !exactDate);
            dialog.set_df_property("cutoff_date", "reqd", exactDate);
        };
        let saving = false;
        dialog = new frappe.ui.Dialog({
            title: __("Crear período en borrador"),
            fields: [
                {fieldtype: "HTML", options: `<p>${__("Confirme el mes de cobranza: puede ser distinto del mes de aplicación. Se creará un período vacío en Borrador, sin conciliar ni reasignar movimientos.")}</p>`},
                {fieldname: "employer", fieldtype: "Link", options: "CN Employer", label: __("Empresa"), read_only: 1, default: defaults.employer},
                {fieldname: "payroll_month", fieldtype: "Date", label: __("Mes de cobranza"), reqd: 1, default: defaults.payroll_month, onchange: updateFields},
                {fieldname: "collection_cycle", fieldtype: "Select", label: __("Ciclo de cobranza"),
                    options: defaults.payroll_frequency === "Quincenal" ? "\nPrimera quincena\nSegunda quincena\nFecha exacta" : "Mensual\nFecha exacta",
                    default: defaults.payroll_frequency === "Quincenal" ? "" : "Mensual", onchange: updateFields},
                {fieldname: "cutoff_date", fieldtype: "Date", label: __("Fecha de corte"),
                    description: __("Elija un día del mes de cobranza para este período adicional.")},
                {fieldname: "historical_scope", fieldtype: "Select", label: __("Tipo de período histórico"),
                    options: "Mensual\nFecha exacta\nRango de fechas", default: defaults.historical_scope, onchange: updateFields},
                {fieldname: "historical_application_date", fieldtype: "Date", label: __("Fecha exacta de aplicación"), default: defaults.historical_application_date},
                {fieldname: "historical_start_date", fieldtype: "Date", label: __("Desde fecha de aplicación"), default: defaults.historical_start_date},
                {fieldname: "historical_end_date", fieldtype: "Date", label: __("Hasta fecha de aplicación"), default: defaults.historical_end_date},
                {fieldname: "remark", fieldtype: "Small Text", label: __("Observaciones")},
            ],
            primary_action_label: __("Crear borrador"),
            async primary_action(values) {
                if (saving) return;
                saving = true;
                try {
                    const result = await frappe.call({
                        method: "credinomina_reconciliation.accounting_period.create_draft_period",
                        args: {import_name: frm.doc.name, values}, freeze: true,
                        freeze_message: __("Creando período en borrador..."),
                    });
                    dialog.hide();
                    frappe.show_alert({message: __("Período creado en Borrador"), indicator: "green"});
                    frappe.set_route("Form", "CN Reconciliation Period", result.message.name);
                } finally {
                    saving = false;
                }
            },
        });
        updateFields();
        dialog.show();
    } finally {
        frm._creating_period = false;
    }
}

function importExceptionRows(rows) {
    // Match CNAccountingImport.recalculate_summary: a file exception is not
    // necessarily an import error or a CN Reconciliation Exception document.
    return (rows || []).flatMap(row => {
        const reasons = [];
        if (["Ambiguo", "Sin coincidencia"].includes(row.match_status)) {
            reasons.push({stage: __("Identificación / vinculación"),
                reason: row.match_reason || __("No hay una coincidencia única. Revise cliente, crédito, empresa y período.")});
        }
        if (row.event_type === "Aplicacion" && Number(row.effective)
                && !["Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"].includes(row.deposit_match_status)) {
            if (!["Conciliado", "Ambiguo", "Sin coincidencia"].includes(row.match_status)) {
                reasons.push({stage: __("Aplicación"),
                    reason: row.match_reason || __("La aplicación aún no tiene un vínculo confirmado con el período o la cobranza.")});
            }
            reasons.push({stage: __("Depósito de la aplicación"),
                reason: row.deposit_match_reason || __("Falta conciliar el depósito de esta aplicación. Registre o complete su distribución y vuelva a conciliar.")});
        }
        if (row.event_type === "Deposito" && Number(row.effective)
                && Number(row.unallocated_usd || 0) > 0.005) {
            reasons.push({stage: __("Distribución del depósito"),
                reason: row.allocation_reason || __("El depósito tiene saldo sin distribuir. Revise sus destinos y justifique cualquier excedente.")});
        }
        return reasons.length ? [{row, reasons}] : [];
    });
}

function accountingImportIssueRecords(row, doc) {
    const records = [], seen = new Set();
    const add = (doctype, route, name, label) => {
        if (typeof name !== "string" || !name.trim() || seen.has(`${doctype}/${name}`)) return;
        seen.add(`${doctype}/${name}`);
        records.push({doctype, route, name: String(name), label});
    };
    add("CN Remittance Allocation", "cn-remittance-allocation", row.remittance_allocation, __("Depósito"));
    add("CN Complementary Item", "cn-complementary-item", row.complementary_item, __("Partida"));
    add("CN Reconciliation Period", "cn-reconciliation-period", row.historical_period, __("Período de la fila"));
    add("CN Reconciliation Period", "cn-reconciliation-period", row.collection_period, __("Período de cobranza"));
    if (!row.historical_period && !row.collection_period) {
        add("CN Reconciliation Period", "cn-reconciliation-period", doc.historical_period, __("Período predeterminado"));
    }
    // Only use explicit saved IDs. A bank reference is not a deposit document name.
    for (const field of ["application_allocation_detail", "allocation_detail", "historical_detail"]) {
        let entries;
        try { entries = typeof row[field] === "string" ? JSON.parse(row[field] || "[]") : row[field] || []; }
        catch (_) { continue; }
        if (!Array.isArray(entries)) continue;
        for (const entry of entries) {
            if (!entry || typeof entry !== "object") continue;
            add("CN Reconciliation Period", "cn-reconciliation-period", entry.period || entry.periodo, __("Período"));
            add("CN Complementary Item", "cn-complementary-item", entry.partida || entry.saldo_a_favor, __("Partida"));
            add("CN Remittance Allocation", "cn-remittance-allocation", entry.deposito_origen, __("Depósito de origen"));
        }
    }
    return records;
}

function accountingImportIssueAction(row, doc, stage) {
    if (!doc.employer) return {field: "employer", text: __("Complete Empresa en la cabecera y guarde antes de conciliar.")};
    if (stage === __("Distribución del depósito")) return {field: "rows", text: __("Revise Destinos e importes en el depósito vinculado y vuelva a conciliar ese depósito.")};
    if (stage === __("Depósito de la aplicación")) return {field: "rows", text: row.deposit_match_status === "Depósito parcial"
        ? __("Complete la evidencia del importe pendiente en los destinos del depósito y vuelva a conciliarlo.")
        : __("Identifique el depósito que paga esta aplicación y revise sus destinos. Use Ver registros relacionados para buscar pendientes del cliente.")};
    if (!row.loan_number && row.event_type === "Aplicacion") return {field: "rows", text: __("Revise Nro. Crédito de esta fila según el respaldo del movimiento.")};
    if (!row.historical_period && !row.collection_period && !doc.historical_period) {
        return {field: "historical_period", text: __("Seleccione el período de la fila o el Período predeterminado de aplicaciones y concilie su vínculo.")};
    }
    return {field: "rows", text: __("Corrija en la fila el dato señalado en el motivo y revise su cliente, crédito, referencia o período. Después guarde y actualice la conciliación.")};
}

function accountingImportIssuesHtml(doc, dirty = false, filters = {}) {
    if (doc.docstatus === 2) return "";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const allExceptions = importExceptionRows(doc.rows);
    const query = String(filters.query || "").trim().toLocaleLowerCase();
    const exceptions = query ? allExceptions.filter(({row, reasons}) => [
        row.idx, row.source_row, row.client_name, row.client_number, row.loan_number,
        row.reference, row.accounting_entry, row.receipt, row.match_status, row.deposit_match_status,
        ...accountingImportIssueRecords(row, doc).map(record => record.name),
        ...reasons.flatMap(({stage, reason}) => [stage, reason, accountingImportIssueAction(row, doc, stage).text]),
    ].join(" ").toLocaleLowerCase().includes(query)) : allExceptions;
    const pages = Math.max(1, Math.ceil(exceptions.length / 25));
    const page = Math.max(0, Math.min(Number(filters.page) || 0, pages - 1));
    const notices = [];
    if (dirty) notices.push(__("Hay cambios sin guardar. Los motivos corresponden a la última conciliación; guarde y vuelva a conciliar para actualizarlos."));
    if (!doc.employer) notices.push(__("Falta la Empresa de la importación."));
    if (!(doc.rows || []).length) notices.push(__("Todavía no hay movimientos cargados. Revise Archivo, moneda y tipo de cambio y use Cargar movimientos contables."));
    if (!allExceptions.length && doc.status === "Importado con excepciones") {
        notices.push(__("El estado guardado indica excepciones, pero las filas actuales no muestran pendientes. Actualice la conciliación para revisar ese estado."));
    }
    if (!allExceptions.length && !notices.length) return "";
    const body = exceptions.slice(page * 25, (page + 1) * 25).map(({row, reasons}) => {
        const records = accountingImportIssueRecords(row, doc);
        const action = accountingImportIssueAction(row, doc, reasons[0].stage);
        const actionLabel = action.field === "employer" ? __("Revisar empresa")
            : action.field === "historical_period" ? __("Revisar período") : __("Revisar fila");
        const link = record => `<div><span class="text-muted">${esc(record.label)}:</span> <a href="/app/${record.route}/${encodeURIComponent(record.name)}" target="_blank" rel="noopener noreferrer">${esc(record.name)}</a></div>`;
        return `<tr><td>${esc(__("Fila"))} ${esc(row.idx || "—")}${row.source_row ? `<div class="small text-muted">${esc(__("Fila del archivo"))}: ${esc(row.source_row)}</div>` : ""}</td>
            <td>${esc(row.client_name || __("Sin identificar"))}<div>${esc(__("Cliente"))}: ${esc(row.client_number || "—")}</div><div>${esc(__("Crédito"))}: ${esc(row.loan_number || "—")}</div></td>
            <td>${records.map(link).join("") || `<span class="text-muted">${esc(__("Sin registro vinculado"))}</span>`}
                ${row.event_type === "Aplicacion" && !row.remittance_allocation && reasons.some(reason => reason.stage === __("Depósito de la aplicación"))
                    ? `<div class="text-muted">${esc(__("Depósito: por identificar o completar su distribución"))}</div>` : ""}</td>
            <td>${reasons.map(({stage, reason}) => `<div style="margin-bottom:10px"><strong>${esc(stage)}</strong><div>${esc(reason)}</div>
                <div class="small text-muted">${esc(accountingImportIssueAction(row, doc, stage).text)}</div></div>`).join("")}</td>
            <td><button type="button" class="btn btn-default btn-xs" data-cn-import-issue-row="${esc(row.name || "")}" data-cn-import-issue-index="${esc(row.idx || "")}" data-cn-import-issue-field="${esc(action.field)}">${esc(actionLabel)}</button>
                ${row.name && row.event_type === "Aplicacion" && Number(row.effective) && row.match_status !== "Ignorado"
                    ? `<button type="button" class="btn btn-default btn-xs" style="margin-top:6px" data-cn-import-related="${esc(row.name)}">${esc(__("Ver registros relacionados"))}</button>` : ""}</td></tr>`;
    }).join("");
    return `<section class="cn-accounting-issues"><strong>${esc(__("Pendientes de conciliación"))}</strong>
        ${notices.length ? `<ul>${notices.map(text => `<li>${esc(text)}</li>`).join("")}</ul>` : ""}
        ${allExceptions.length ? `<p class="small" role="status">${esc(__("Filas filtradas: {0} de {1}. Cada motivo señala qué revisar; los registros enlazados conservan sus permisos de acceso.", [exceptions.length, allExceptions.length]))}</p>
        <div class="table-responsive" style="max-height:65vh;overflow:auto"><table class="table table-bordered" style="font-size:var(--text-base,14px);min-width:760px"><thead style="position:sticky;top:0;background:var(--card-bg);z-index:1"><tr>
            ${["Fila", "Cliente / crédito", "Registro relacionado", "Qué falta / dato que debe corregir", "Acciones"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>
            ${!exceptions.length ? `<p>${esc(__("No hay pendientes que coincidan con la búsqueda."))}</p>` : `
            <div class="flex my-3" style="gap:12px;align-items:center;flex-wrap:wrap">
                <button type="button" class="btn btn-default btn-sm" data-cn-import-issues-page="${page - 1}" ${page === 0 ? "disabled" : ""}>${esc(__("Anterior"))}</button>
                <span>${esc(__("Página {0} de {1} · Mostrando {2}–{3} de {4}", [page + 1, pages, page * 25 + 1, Math.min((page + 1) * 25, exceptions.length), exceptions.length]))}</span>
                <button type="button" class="btn btn-default btn-sm" data-cn-import-issues-page="${page + 1}" ${page === pages - 1 ? "disabled" : ""}>${esc(__("Siguiente"))}</button>
            </div>`}
            <button type="button" class="btn btn-default btn-sm" data-cn-import-all-issues>${esc(__("Filtrar por estado, etapa o importe"))}</button>
            ` : ""}
        <p class="small text-muted" style="margin-top:10px">${esc(__("Motivos de los datos guardados, según la última conciliación. Una aplicación puede tener pendiente de depósito aunque ya esté vinculada al período."))}</p></section>`;
}

function showAccountingImportIssues(frm) {
    const dialog = new frappe.ui.Dialog({title: __("Pendientes de conciliación"), size: "extra-large",
        fields: [{fieldname: "pending_diagnostics", fieldtype: "HTML"}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide()});
    renderAccountingImportIssues(frm, dialog.get_field("pending_diagnostics").$wrapper, () => dialog.hide());
    dialog.show();
}

function renderAccountingImportIssues(frm, wrapper, beforeNavigate = () => {}) {
    if (!wrapper) return;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const state = frm._accounting_issues_state?.name === frm.doc.name
        ? frm._accounting_issues_state : {name: frm.doc.name, query: "", page: 0};
    frm._accounting_issues_state = state;
    wrapper.html(`<div class="mb-3"><label style="display:block">${esc(__("Buscar pendientes"))}
        <input type="search" class="form-control cn-import-issues-search" style="max-width:600px;margin-top:6px"
            placeholder="${esc(__("Cliente, crédito, fila, registro relacionado o motivo"))}" value="${esc(state.query)}"></label></div>
        <div class="cn-import-issues-content"></div>`);
    const render = () => {
        const html = accountingImportIssuesHtml(frm.doc, frm.is_dirty(), state);
        wrapper.find(".cn-import-issues-content").html(html || `<p class="text-muted">${esc(frm.doc.docstatus === 2
            ? __("La importación está cancelada.") : __("No hay pendientes en las filas actuales según la última conciliación."))}</p>`);
    };
    bindAccountingImportIssues(frm, wrapper, render, beforeNavigate);
    render();
}

function bindAccountingImportIssues(frm, wrapper, render, beforeNavigate = () => {}) {
    if (!wrapper) return;
    const name = frm.doc.name;
    wrapper.off(".cnImportIssues");
    wrapper.on("input.cnImportIssues", ".cn-import-issues-search", event => {
        frm._accounting_issues_state.query = event.target.value;
        frm._accounting_issues_state.page = 0;
        render();
    });
    wrapper.on("click.cnImportIssues", "[data-cn-import-issues-page]", event => {
        const state = frm._accounting_issues_state;
        state.page = Math.max(0, Number(event.currentTarget.dataset.cnImportIssuesPage) || 0);
        render();
    });
    wrapper.on("click.cnImportIssues", "[data-cn-import-all-issues]", () => showImportExceptions(frm));
    wrapper.on("click.cnImportIssues", "[data-cn-import-related]", function () {
        showAccountingRelatedRecords(frm, this.dataset.cnImportRelated, $(this));
    });
    wrapper.on("click.cnImportIssues", "[data-cn-import-issue-row]", async event => {
        if (frm.doc.name !== name) { beforeNavigate(); return; }
        const input = event.currentTarget;
        beforeNavigate();
        if (["employer", "historical_period"].includes(input.dataset.cnImportIssueField)) {
            await frm.scroll_to_field(input.dataset.cnImportIssueField);
            return;
        }
        const row = (frm.doc.rows || []).find(row => row.name === input.dataset.cnImportIssueRow
            || (!input.dataset.cnImportIssueRow && String(row.idx) === input.dataset.cnImportIssueIndex));
        await frm.scroll_to_field("rows");
        // Grid methods vary by Framework version; scrolling remains available.
        const grid = frm.fields_dict?.rows?.grid;
        const gridRow = row && grid?.grid_rows_by_docname?.[row.name];
        if (gridRow?.toggle_view) gridRow.toggle_view(true);
    });
}

function updateImportExceptionNotice(frm) {
    const exceptions = importExceptionRows(frm.doc.rows);
    if (!exceptions.length && frm.doc.status !== "Importado con excepciones") {
        frm.set_intro("");
        return;
    }
    frm.set_intro(exceptions.length
        ? __("Esta carga tiene {0} filas con pendientes detectados. Use Consultas → Revisar pendientes para conocer los motivos. Una aplicación sin depósito no es un error de importación ni una excepción documentada.", [exceptions.length])
        : __("La carga conserva el estado «Importado con excepciones», pero las filas actuales no muestran pendientes. Revise el detalle y use «Conciliar período predeterminado» para actualizar el resultado."), "orange");
}

function importExceptionAmounts(row, doc) {
    const cents = value => Math.round((Number(value || 0) + Number.EPSILON) * 100);
    const amount = row.amount_usd != null ? cents(row.amount_usd) : row.currency === "USD" ? cents(row.amount) : null;
    if (row.event_type !== "Aplicacion") return {amount, adjustment: null, net: null,
        paid: row.event_type === "Deposito" ? amount : null,
        pending: row.event_type === "Deposito" ? cents(row.unallocated_usd) : null};
    const adjustment = cents(row.application_adjustment_usd);
    const net = row.net_applied_usd != null ? cents(row.net_applied_usd) : amount == null ? null : Math.max(amount - adjustment, 0);
    const historical = row.historical_period || row.processing_route === "Historica" || doc.historical_period || doc.historical_backfill;
    let paid = null, pending = null;
    if (historical && row.match_status === "Conciliado" && row.historical_period) {
        paid = cents(row.historical_remitted_usd);
        pending = cents(row.historical_balance_usd);
    } else if (historical || row.deposit_match_status === "Sin deposito") {
        paid = 0;
        pending = net;
    }
    // Operational cash can cover several applications sharing a collection.
    // Do not parse a reason string or invent a per-application allocation.
    return {amount, adjustment, net, paid, pending};
}

function importExceptionFilterError(filters) {
    for (const field of ["min_amount", "max_amount"]) {
        if (filters[field] == null || String(filters[field]).trim() === "") continue;
        if (!/^-?\d+(\.\d{1,2})?$/.test(String(filters[field]).trim())) {
            return __("Indique montos con hasta dos decimales, usando punto decimal y sin separadores de miles.");
        }
    }
    const min = filters.min_amount, max = filters.max_amount;
    if (min != null && max != null && String(min).trim() !== "" && String(max).trim() !== "" && Number(min) > Number(max)) {
        return __("Monto desde no puede ser mayor que Monto hasta.");
    }
    return "";
}

function filterImportExceptions(frm, filters = {}) {
    if (importExceptionFilterError(filters)) return [];
    const normalize = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    const terms = normalize(filters.search).trim().split(/\s+/).filter(Boolean);
    return importExceptionRows(frm.doc.rows).map(entry => ({...entry,
        amounts: importExceptionAmounts(entry.row, frm.doc),
        status: entry.row.deposit_match_status || entry.row.match_status || __("Pendiente"),
    })).filter(entry => {
        const {row, reasons, amounts, status} = entry;
        const text = normalize([row.idx, row.client_name, row.client_number, row.loan_number,
            row.portfolio_credit_status, row.portfolio_snapshot_used,
            row.accounting_entry, row.receipt, row.reference, ...reasons.map(reason => reason.reason)].join(" "));
        if (!terms.every(term => text.includes(term))) return false;
        if (filters.state && status !== filters.state) return false;
        if (filters.stage && !reasons.some(reason => reason.stage === filters.stage)) return false;
        for (const [field, minimum] of [["min_amount", true], ["max_amount", false]]) {
            if (filters[field] == null || String(filters[field]).trim() === "") continue;
            const limit = Math.round(Number(filters[field]) * 100);
            if (amounts.amount == null || (minimum ? amounts.amount < limit : amounts.amount > limit)) return false;
        }
        return true;
    });
}

function importExceptionsHtml(frm, filters = {}, offset = 0) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const error = importExceptionFilterError(filters);
    if (error) return `<p class="text-danger" role="alert">${esc(error)}</p>`;
    const total = importExceptionRows(frm.doc.rows).length;
    const exceptions = filterImportExceptions(frm, filters);
    const money = value => value == null ? "—" : esc(new Intl.NumberFormat("es-NI", {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(value / 100));
    const numeric = value => `<td class="text-right text-nowrap">${money(value)}</td>`;
    return `<p role="status">${esc(__("Filas filtradas: {0} de {1}", [exceptions.length, total]))}</p>
        ${exceptions.length ? `
            <div style="max-height:55vh;overflow:auto"><table class="table table-bordered">
                <thead style="position:sticky;top:0;background:var(--fg-color,white);z-index:1"><tr>
                    ${["Fila", "Cliente / crédito", "Monto US$", "Ajustes US$", "Aplicado neto US$", "Depositado US$", "Pendiente US$", "Estado / motivo"].map(label => `<th>${esc(__(label))}</th>`).join("")}
                </tr></thead>
                <tbody>${exceptions.slice(offset, offset + 50).map(({row, reasons, amounts, status}) => `<tr>
                    <td>${esc(row.idx)}</td>
                    <td>${esc(row.client_name)}<br>${esc(__("Nro. Cliente"))}: ${esc(row.client_number)}<br>${esc(__("Crédito"))}: ${esc(row.loan_number)}
                        ${row.event_type === "Aplicacion" ? `<br>${esc(__("Estado del crédito en corte"))}: ${esc(row.portfolio_credit_status || "—")}
                            <br>${esc(__("Corte utilizado"))}: ${esc(row.portfolio_snapshot_used || "—")}` : ""}</td>
                    ${[amounts.amount, amounts.adjustment, amounts.net, amounts.paid, amounts.pending].map(numeric).join("")}
                    <td style="white-space:normal;min-width:200px">${esc(status)}<details><summary>${esc(__("Ver motivos"))}</summary>
                        ${reasons.map(({stage, reason}) => `<p><strong>${esc(stage)}</strong><br>${esc(reason)}</p>`).join("")}</details></td>
                </tr>`).join("")}</tbody>
            </table></div>
            <div class="d-flex justify-content-between align-items-center">
                <button class="btn btn-default btn-sm" data-exception-page="previous" ${offset ? "" : "disabled"}>${esc(__("Anterior"))}</button>
                <span>${esc(__("Mostrando {0}–{1} de {2}", [offset + 1, Math.min(offset + 50, exceptions.length), exceptions.length]))}</span>
                <button class="btn btn-default btn-sm" data-exception-page="next" ${offset + 50 < exceptions.length ? "" : "disabled"}>${esc(__("Siguiente"))}</button>
            </div>`
            : `<p>${esc(total ? __("No hay filas que coincidan con los filtros.") : __("No hay pendientes en las filas actuales. Si el estado guardado no coincide, use «Conciliar período predeterminado» para recalcularlo."))}</p>`}
        <p class="text-muted mt-3">${esc(__("Fila corresponde al número en la tabla de esta importación. Los importes están en US$. — indica un importe no disponible por aplicación; en operativo un depósito puede cubrir aplicaciones agrupadas. Consulte Ver motivos."))}</p>
        <p class="text-muted">${esc(__("Esta consulta no modifica datos ni concilia. Una fila aparece una sola vez aunque tenga varios motivos."))}</p>`;
}

function showImportExceptions(frm) {
    let dialog, offset = 0;
    const exceptions = importExceptionRows(frm.doc.rows);
    const values = () => Object.fromEntries(["search", "state", "stage", "min_amount", "max_amount"].map(field => [field, dialog.get_value(field)]));
    const render = () => dialog.get_field("exception_list").$wrapper.html(importExceptionsHtml(frm, values(), offset));
    const changed = () => { if (dialog) { offset = 0; render(); } };
    dialog = new frappe.ui.Dialog({
        title: __("Pendientes detectados de {0}", [frm.doc.name]), size: "extra-large",
        fields: [
            {fieldtype: "Data", fieldname: "search", label: __("Buscar"), description: __("Cliente, crédito, estado en corte, corte, asiento, recibo, fila o motivo"), onchange: changed},
            {fieldtype: "Column Break"},
            {fieldtype: "Select", fieldname: "state", label: __("Estado"), options: ["", ...new Set(exceptions.map(({row}) => row.deposit_match_status || row.match_status || __("Pendiente")))], onchange: changed},
            {fieldtype: "Column Break"},
            {fieldtype: "Select", fieldname: "stage", label: __("Etapa"), options: ["", ...new Set(exceptions.flatMap(({reasons}) => reasons.map(reason => reason.stage)))], onchange: changed},
            {fieldtype: "Section Break", label: __("Rango de monto US$ (opcional)"), collapsible: 1},
            {fieldtype: "Data", fieldname: "min_amount", label: __("Monto desde"), description: __("Ejemplo: 26.01. Vacío: sin límite."), onchange: changed},
            {fieldtype: "Column Break"},
            {fieldtype: "Data", fieldname: "max_amount", label: __("Monto hasta"), onchange: changed},
            {fieldtype: "Section Break"},
            {fieldtype: "HTML", fieldname: "exception_list", options: importExceptionsHtml(frm)},
        ],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        secondary_action_label: __("Limpiar filtros"), secondary_action: async () => {
            await dialog.set_values({search: "", state: "", stage: "", min_amount: "", max_amount: ""});
            changed();
        },
    });
    dialog.get_field("exception_list").$wrapper.on("click", "[data-exception-page]", event => {
        const direction = event.currentTarget.getAttribute("data-exception-page");
        const count = filterImportExceptions(frm, values()).length;
        offset = direction === "next" ? Math.min(offset + 50, Math.max(0, Math.ceil(count / 50) - 1) * 50) : Math.max(0, offset - 50);
        render();
    });
    dialog.show();
}

async function reconcileCompany(frm) {
    if (frm.__company_reconciliation_running) return;
    if (!frm.doc.employer) {
        frappe.msgprint(__("Seleccione la empresa antes de conciliar."));
        return;
    }
    if (!frm.doc.historical_period) {
        frappe.msgprint(__("Seleccione el Período predeterminado de aplicaciones antes de conciliar."));
        return;
    }
    frm.__company_reconciliation_running = true;
    try {
        if (frm.is_dirty()) await frm.save();
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.reconcile_company_sources",
            args: {import_name: frm.doc.name},
            freeze: true,
            freeze_message: __("Conciliando el período {0}…", [frm.doc.historical_period]),
        });
        await frm.reload_doc();
        showCompanyReconciliation(response.message);
    } finally {
        frm.__company_reconciliation_running = false;
    }
}

function showCompanyReconciliation(result) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const counts = [
        [__("Filas procesadas"), result.rows], [__("Conciliadas"), result.matched],
        [__("Pendientes"), result.pending], [__("Ignoradas"), result.ignored],
    ];
    const pending = result.pending_rows || [];
    const matched = result.matched_rows || [];
    const renderMovements = (movements, reconciled) => `
        <div style="max-height:40vh;overflow:auto"><table class="table table-bordered">
        <thead><tr><th>${__("Importación / fila")}</th><th>${__("Cliente / crédito")}</th><th>${__("Estado")}</th><th>${__("Detalle")}</th></tr></thead>
        <tbody>${movements.map(row => `<tr${reconciled ? ' class="cn-row-reconciled"' : ""}>
            <td><a href="/app/cn-accounting-import/${encodeURIComponent(row.import_name)}">${esc(row.import_name)}</a><br>${__("Fila")} ${esc(row.row)}</td>
            <td>${esc(row.client_name)}<br><span class="text-muted">${esc(row.loan_number)}</span></td>
            <td>${reconciled ? __("Conciliado") : __("Pendiente")}</td>
            <td style="white-space:normal">${esc(row.reason)}</td>
        </tr>`).join("")}</tbody></table></div>`;
    const dialog = new frappe.ui.Dialog({
        title: __("Conciliación de {0}", [result.period || result.employer]), size: "extra-large",
        fields: [{fieldtype: "HTML", options: `
            <style>.cn-company-reconciliation .cn-row-reconciled > td {
                background-color: var(--bg-green, #eaf6ec);
                color: var(--text-color, #1f272e);
            }</style>
            <div class="cn-company-reconciliation">
            <p>${__("Se procesaron {0} importaciones con los datos guardados.", [esc(result.imports)])}</p>
            ${result.period ? `<p>${__("Período procesado")}: ${esc(result.period)}. ${__("Se conservaron las distribuciones de depósitos registradas.")}</p>` : (result.reconciled_employers || []).length > 1 ? `<p>${__("Se recalcularon juntas las empresas vinculadas por pagos compartidos para conservar sus saldos:")} ${result.reconciled_employers.map(esc).join(", ")}</p>` : ""}
            <div class="row">${counts.map(([label, value]) => `<div class="col-sm-3"><div class="text-muted">${label}</div><h3>${esc(value)}</h3></div>`).join("")}</div>
            <p class="text-muted">${__("Las filas ignoradas, como duplicados y ajustes, no se cuentan como pendientes.")}</p>
            ${result.pending ? `<h5>${__("Motivos para revisar")}</h5>
                ${renderMovements(pending, false)}
                ${result.pending > pending.length ? `<p>${__("Se muestran {0} de {1} filas pendientes. Abra las importaciones para revisar las demás.", [esc(pending.length), esc(result.pending)])}</p>` : ""}`
                : `<p class="text-success">${result.rows ? __("No quedaron filas pendientes de conciliación.") : __("Este período todavía no tiene aplicaciones vinculadas.")}</p>`}
            ${matched.length ? `<h5>${__("Movimientos conciliados")}</h5>
                ${renderMovements(matched, true)}
                ${result.matched > matched.length ? `<p>${__("Se muestran {0} de {1} filas conciliadas. Abra las importaciones para revisar las demás.", [esc(matched.length), esc(result.matched)])}</p>` : ""}` : ""}
            </div>
        `}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        secondary_action_label: __("Ver importaciones"),
        secondary_action: () => {
            dialog.hide();
            frappe.set_route("List", "CN Accounting Import", {employer: result.employer, ...(result.period ? {historical_period: result.period} : {})});
        },
    });
    dialog.show();
}
