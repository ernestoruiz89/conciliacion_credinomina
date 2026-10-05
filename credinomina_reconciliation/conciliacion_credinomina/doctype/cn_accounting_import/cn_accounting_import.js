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
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer, status: ["!=", "Cerrado"] },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer, status: ["!=", "Cerrado"] },
        }));
        frm.set_query("portfolio_snapshot", () => ({
            query: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.get_company_portfolio_snapshots",
            filters: {employer: frm.doc.employer},
        }));
        frm.set_df_property("rows", "label", __("Aplicaciones de pago por cliente"));
        updateImportExceptionNotice(frm);
        loadAccountingClientSummary(frm);
        if (frm.is_new()) return;

        if (frappe.model.can_create("CN Reconciliation Period")) {
            frm.add_custom_button(__("Crear período"), () => createAccountingPeriod(frm));
        }

        frm.add_custom_button(__("3. Cargar movimientos contables"), async () => {
            if (frm.is_dirty()) await frm.save();
            return frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.import_source_file",
                args: { import_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando aplicaciones y actualizando saldos sin redistribuir depósitos..."),
            }).then(response => {
                frappe.show_alert({
                    message: __("Movimientos cargados. Se conservaron las distribuciones de depósitos. Para cambiarlas, use Conciliar en el depósito."),
                    indicator: "green",
                }, 8);
                const name = response.message?.import_name;
                if (name && name !== frm.doc.name) {
                    return frappe.set_route("Form", frm.doctype, name);
                }
                return frm.reload_doc();
            });
        });

        if (frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Conciliar esta empresa"), () => reconcileCompany(frm));
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

function accountingClientSummaryTable(rows, page = 0) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const fields = ["applied_usd", "assigned_usd", "rounding_usd", "balance_usd"];
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const total = field => rows.some(row => row[field] == null) ? null
        : rows.reduce((sum, row) => sum + Math.round(Number(row[field]) * 100), 0) / 100;
    const body = rows.slice(page * 50, (page + 1) * 50).map(row => {
        const color = row.status === "Conciliado" ? "var(--green-50, #edfdf3)" : row.status === "Revisar" ? "var(--yellow-50, #fffae6)" : "transparent";
        return `<tr style="background:${color}"><td><strong>${esc(row.client_name)}</strong>
            <div class="text-muted">${esc((row.loans || []).join(", "))}</div>
            ${(row.observations || []).map(note => `<div class="text-warning">${esc(note)}</div>`).join("")}</td>
            <td>${esc(row.client_number || "—")}</td>
            ${fields.map(field => `<td class="text-right" style="white-space:nowrap">${amount(row[field])}</td>`).join("")}
            <td>${esc(__(row.status))}</td></tr>`;
    }).join("");
    return `<div class="table-responsive"><table class="table table-bordered table-hover" style="font-size:var(--text-base, 14px)">
        <thead><tr>${["Cliente / créditos", "Nro. Cliente", "Aplicado neto US$", "Asignado a depósitos US$", "Ajuste de centavos US$", "Saldo US$", "Estado"].map((label, i) => `<th class="${i >= 2 && i <= 5 ? "text-right" : ""}">${esc(__(label))}</th>`).join("")}</tr></thead>
        <tbody>${body || `<tr><td colspan="7">${esc(__("No hay clientes que coincidan."))}</td></tr>`}</tbody>
        <tfoot><tr><th colspan="2">${esc(__("Total de clientes filtrados"))} (${rows.length})</th>
        ${fields.map(field => `<th class="text-right" style="white-space:nowrap">${amount(total(field))}</th>`).join("")}<th></th></tr></tfoot>
    </table></div>`;
}

async function loadAccountingClientSummary(frm) {
    const wrapper = frm.fields_dict?.client_summary_html?.$wrapper;
    if (!wrapper) return;
    const request = frm._client_summary_request = (frm._client_summary_request || 0) + 1;
    const name = frm.doc.name;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.off(".cnClientSummary");
    if (frm.is_new() || frm.is_dirty()) {
        wrapper.html(`<p class="text-muted">${esc(__("Guarde el documento para consultar los saldos por cliente."))}</p>`);
        return;
    }
    wrapper.html(`<p class="text-muted">${esc(__("Consultando saldos por cliente..."))}</p>`);
    const current = () => frm._client_summary_request === request && frm.doc.name === name;
    try {
        const response = await frappe.call({method: "credinomina_reconciliation.accounting_client_summary.get_client_summary", args: {import_name: name}});
        if (!current()) return;
        if (frm.is_dirty()) {
            wrapper.html(`<p class="text-muted">${esc(__("Hay cambios sin guardar. Guarde para actualizar los saldos."))}</p>`);
            return;
        }
        const rows = response.message.rows || [];
        wrapper.html(`<p class="text-muted">${esc(__("Solo aplicaciones efectivas de esta importación. El aplicado neto ya descuenta los ajustes confirmados; no incluye cobranza ni saldos a favor sin vincular. Datos guardados, según la última conciliación."))}</p>
            <div class="flex mb-3" style="gap:12px;align-items:center;flex-wrap:wrap">
                <input class="form-control input-sm cn-client-search" style="max-width:360px" aria-label="${esc(__("Buscar cliente, número o crédito"))}" placeholder="${esc(__("Buscar cliente, número o crédito"))}">
                <button type="button" class="btn btn-default btn-sm cn-client-refresh">${esc(__("Actualizar resumen"))}</button>
            </div><div class="cn-client-table"></div>
            <div class="flex" style="gap:12px;align-items:center"><button type="button" class="btn btn-default btn-sm cn-client-prev">${esc(__("Anterior"))}</button>
                <span class="cn-client-page"></span><button type="button" class="btn btn-default btn-sm cn-client-next">${esc(__("Siguiente"))}</button></div>
            <p class="text-muted mt-2">${esc(__("Saldo = aplicado neto + ajuste de centavos − asignado. No se compensan saldos entre créditos. «—» indica que falta determinar un importe; el total tampoco se presenta como completo."))}</p>`);
        let page = 0, query = "";
        const render = () => {
            const filtered = rows.filter(row => [row.client_name, row.client_number, ...(row.loans || [])].join(" ").toLocaleLowerCase().includes(query));
            const pages = Math.max(1, Math.ceil(filtered.length / 50));
            page = Math.max(0, Math.min(page, pages - 1));
            wrapper.find(".cn-client-table").html(accountingClientSummaryTable(filtered, page));
            wrapper.find(".cn-client-page").text(__("Página {0} de {1}", [page + 1, pages]));
            wrapper.find(".cn-client-prev").prop("disabled", page === 0);
            wrapper.find(".cn-client-next").prop("disabled", page === pages - 1);
        };
        wrapper.on("input.cnClientSummary", ".cn-client-search", event => { query = event.target.value.trim().toLocaleLowerCase(); page = 0; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-prev", () => { page--; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-next", () => { page++; render(); });
        wrapper.on("click.cnClientSummary", ".cn-client-refresh", () => loadAccountingClientSummary(frm));
        render();
    } catch (error) {
        if (!current()) return;
        wrapper.html(`<p class="text-danger">${esc(__("No se pudo consultar el resumen. Revise sus permisos o vuelva a intentar."))}</p><button type="button" class="btn btn-default btn-sm cn-client-retry">${esc(__("Reintentar"))}</button>`);
        wrapper.on("click.cnClientSummary", ".cn-client-retry", () => loadAccountingClientSummary(frm));
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
        };
        let saving = false;
        dialog = new frappe.ui.Dialog({
            title: __("Crear período en borrador"),
            fields: [
                {fieldtype: "HTML", options: `<p>${__("Confirme el mes de cobranza: puede ser distinto del mes de aplicación. Se creará un período vacío en Borrador, sin conciliar ni reasignar movimientos.")}</p>`},
                {fieldname: "employer", fieldtype: "Link", options: "CN Employer", label: __("Empresa"), read_only: 1, default: defaults.employer},
                {fieldname: "payroll_month", fieldtype: "Date", label: __("Mes de cobranza"), reqd: 1, default: defaults.payroll_month, onchange: updateFields},
                {fieldname: "collection_cycle", fieldtype: "Select", label: __("Ciclo de cobranza"),
                    options: defaults.payroll_frequency === "Quincenal" ? "\nPrimera quincena\nSegunda quincena" : "Mensual",
                    default: defaults.payroll_frequency === "Quincenal" ? "" : "Mensual"},
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

function updateImportExceptionNotice(frm) {
    const exceptions = importExceptionRows(frm.doc.rows);
    if (!exceptions.length && frm.doc.status !== "Importado con excepciones") {
        frm.set_intro("");
        return;
    }
    frm.set_intro(exceptions.length
        ? __("Esta carga tiene {0} filas con excepciones de conciliación. Use «Ver excepciones» para conocer los motivos; puede tratarse de depósitos pendientes, no de un error al importar.", [exceptions.length])
        : __("La carga conserva el estado «Importado con excepciones», pero las filas actuales no muestran excepciones. Revise el detalle y use «Conciliar esta empresa» para actualizar el resultado."), "orange");
    frm.add_custom_button(__("Ver excepciones"), () => showImportExceptions(frm));
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
                    <td>${esc(row.client_name)}<br>${__("Nro. Cliente")}: ${esc(row.client_number)}<br>${__("Crédito")}: ${esc(row.loan_number)}</td>
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
            : `<p>${esc(total ? __("No hay filas que coincidan con los filtros.") : __("No hay excepciones en las filas actuales. Si el estado guardado no coincide, use «Conciliar esta empresa» para recalcularlo."))}</p>`}
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
        title: __("Excepciones de {0}", [frm.doc.name]), size: "extra-large",
        fields: [
            {fieldtype: "Data", fieldname: "search", label: __("Buscar"), description: __("Cliente, crédito, asiento, recibo, fila o motivo"), onchange: changed},
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
    frm.__company_reconciliation_running = true;
    try {
        if (frm.is_dirty()) await frm.save();
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.reconcile_company_sources",
            args: {import_name: frm.doc.name},
            freeze: true,
            freeze_message: __("Conciliando las importaciones y depósitos de {0}…", [frm.doc.employer]),
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
        title: __("Conciliación de {0}", [result.employer]), size: "extra-large",
        fields: [{fieldtype: "HTML", options: `
            <style>.cn-company-reconciliation .cn-row-reconciled > td {
                background-color: var(--bg-green, #eaf6ec);
                color: var(--text-color, #1f272e);
            }</style>
            <div class="cn-company-reconciliation">
            <p>${__("Se procesaron {0} importaciones con los datos guardados.", [esc(result.imports)])}</p>
            ${(result.reconciled_employers || []).length > 1 ? `<p>${__("Se recalcularon juntas las empresas vinculadas por pagos compartidos para conservar sus saldos:")} ${result.reconciled_employers.map(esc).join(", ")}</p>` : ""}
            <div class="row">${counts.map(([label, value]) => `<div class="col-sm-3"><div class="text-muted">${label}</div><h3>${esc(value)}</h3></div>`).join("")}</div>
            <p class="text-muted">${__("Las filas ignoradas, como duplicados y ajustes, no se cuentan como pendientes.")}</p>
            ${result.pending ? `<h5>${__("Motivos para revisar")}</h5>
                ${renderMovements(pending, false)}
                ${result.pending > pending.length ? `<p>${__("Se muestran {0} de {1} filas pendientes. Abra las importaciones para revisar las demás.", [esc(pending.length), esc(result.pending)])}</p>` : ""}`
                : `<p class="text-success">${result.rows ? __("No quedaron filas pendientes de conciliación.") : __("Esta empresa todavía no tiene movimientos importados.")}</p>`}
            ${matched.length ? `<h5>${__("Movimientos conciliados")}</h5>
                ${renderMovements(matched, true)}
                ${result.matched > matched.length ? `<p>${__("Se muestran {0} de {1} filas conciliadas. Abra las importaciones para revisar las demás.", [esc(matched.length), esc(result.matched)])}</p>` : ""}` : ""}
            </div>
        `}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        secondary_action_label: __("Ver importaciones"),
        secondary_action: () => {
            dialog.hide();
            frappe.set_route("List", "CN Accounting Import", {employer: result.employer});
        },
    });
    dialog.show();
}
