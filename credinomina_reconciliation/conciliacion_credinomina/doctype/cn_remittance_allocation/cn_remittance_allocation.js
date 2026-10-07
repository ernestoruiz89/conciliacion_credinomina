async function loadPayingCompanies(frm) {
    const payer = frm.doc.employer;
    frm.paying_companies = payer ? [payer] : [];
    if (!payer) return;
    const response = await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.get_paying_companies",
        args: { employer: payer },
    });
    if (frm.doc.employer === payer) frm.paying_companies = response.message || [payer];
}

frappe.ui.form.on("CN Remittance Allocation", {
    setup(frm) {
        // Imported rows remain intact; company and credit can disambiguate identity.
        frm.fields_dict.detail_rows.grid.df.cannot_add_rows = true;
        frm.fields_dict.detail_rows.grid.df.cannot_delete_rows = true;
        frm.set_query("bank_account", () => ({ filters: { active: 1 } }));
        frm.set_query("period", "detail_periods", () => ({ filters: { employer: ["in", frm.paying_companies || [frm.doc.employer]], status: ["!=", "Cerrado"] } }));
        frm.set_query("employer", "detail_rows", () => ({ filters: { name: ["in", frm.paying_companies || [frm.doc.employer]] } }));
        frm.set_query("employer", "targets", () => ({ filters: { name: ["in", frm.paying_companies || [frm.doc.employer]] } }));
    },
    refresh(frm) {
        loadPayingCompanies(frm);
        renderRemittanceOverview(frm);
        renderRemittanceAllocations(frm);
        renderRemittanceDistribution(frm);
        toggleRemittanceDetailActions(frm);
        toggleApplicationDetailAction(frm);
        addCompanyCreditButton(frm);
        addBulkClientCreditButton(frm);
        frm.toggle_display("select_pending_targets", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        frm.toggle_display("create_complementary", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        frm.toggle_display("link_detail_targets", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write") &&
            !!(frm.doc.detail_rows || []).length && !!(frm.doc.targets || []).length);
        frm.toggle_display("select_detail_credit", !frm.is_new() && frm.doc.docstatus !== 2 &&
            !!frm.get_perm(0, "write") && !!(frm.doc.detail_rows || []).length);
        frm.add_custom_button(__("Plantilla de detalle del depósito"), () => downloadRemittanceTemplate(frm), __("Plantillas"));
        if (!frm.is_new()) {
            frm.add_custom_button(__("Historial de conciliación"), () => showReconciliationHistory(frm), __("Conciliación"));
        }
        if (!frm.is_new() && Number(frm.doc.justified_surplus_usd) > 0) {
            frm.add_custom_button(__("Ver saldos a favor"), () => frappe.set_route("List", "CN Complementary Item", {
                registered_deposit: frm.doc.name, category: ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]], docstatus: 1,
            }), __("Conciliación"));
        }
        if (!frm.is_new() && frm.doc.docstatus === 0 && frm.get_perm(0, "submit")) {
            frm.add_custom_button(__("Confirmar depósito"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.savesubmit();
            });
        }
        if (!frm.is_new() && frm.doc.docstatus === 1 && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Corregir datos"), () => correctDepositData(frm));
            frm.add_custom_button(__("Desconciliar"), () => unreconcileRemittance(frm), __("Conciliación"));
            frm.add_custom_button(__("Conciliar"), () => reconcileRemittance(frm), __("Conciliación"));
            frm.add_custom_button(__("Usar conciliación preparada"), () => usePreparedReconciliation(frm), __("Conciliación"));
        }
        const file = frm.doc.detail_file || frm.doc.support_file || "";
        if (frm.is_new() || frm.doc.docstatus === 2 || !/\.(xlsx|xls|csv)(\?|$)/i.test(file)) return;
        if (frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Cargar detalle del depósito"), () => importRemittanceDetail(frm));
        }
    },
    download_detail_template: downloadRemittanceTemplate,
    load_deposit_detail: importRemittanceDetail,
    select_detail_credit: selectRemittanceDetailCredit,
    link_detail_targets: linkRemittanceDetailTargets,
    create_complementary: createRemittanceComplementary,
    refresh_distribution: renderRemittanceDistribution,
    use_applications_detail: useApplicationsAsDetail,
    detail_file(frm) { toggleRemittanceDetailActions(frm); renderRemittanceOverview(frm); },
    support_file: toggleRemittanceDetailActions,
    amount_usd: renderRemittanceOverview,
    employer(frm) { loadPayingCompanies(frm); renderRemittanceOverview(frm); },
    notes: renderRemittanceOverview,
    deposit_amount: updateUsdEquivalent,
    deposit_currency: updateUsdEquivalent,
    fx_rate: updateUsdEquivalent,
    deposit_date: updateUsdEquivalent,
    async bank_account(frm) {
        const document = frm.doc;
        const account = document.bank_account;
        if (!account || account === "NO IDENTIFICADA" || document.docstatus !== 0) return;
        const response = await frappe.db.get_value("CN Bank Account", account, "currency");
        // A slow lookup must not overwrite a newer selection or another document.
        if (frm.doc !== document || frm.doc.bank_account !== account || frm.doc.docstatus !== 0) return;
        const currency = response.message?.currency;
        if (["USD", "NIO"].includes(currency)) {
            // Reuse the currency event to recalculate the USD equivalent.
            await frm.set_value("deposit_currency", currency);
        }
    },
    async select_pending_targets(frm) {
        if (!frm.doc.employer || !Number(frm.doc.amount_usd)) {
            frappe.msgprint(__("Indique la empresa y el importe del depósito (con tasa si está en C$)."));
            return;
        }
        if (frm.is_new() || frm.is_dirty()) {
            const save = await new Promise(resolve => frappe.confirm(
                __("Se guardarán los datos del depósito antes de consultar sus partidas. Esto no lo confirma ni lo concilia. ¿Continuar?"),
                () => resolve(true), () => resolve(false)
            ));
            if (!save) return;
            await frm.save();
        }
        const response = await loadPendingRemittanceTargets(frm);
        new RemittanceTargetPicker(frm, response);
    },
});

async function usePreparedReconciliation(frm) {
    if (frm.is_dirty()) await frm.save();
    const response = await frappe.call({
        method: "credinomina_reconciliation.provisional_adjustments.preview_transfer",
        args: {remittance_name: frm.doc.name}, freeze: true,
        freeze_message: __("Verificando bases, aplicaciones y ajustes aprobados…"),
    });
    const plan = response.message;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => esc(format_currency(value, "USD", 2));
    const dialog = new frappe.ui.Dialog({
        title: __("Trasladar conciliación preparada al depósito"), size: "extra-large",
        fields: [
            {fieldtype: "HTML", fieldname: "preview", options: `
                <p>${esc(__("Se usará la cobranza cuando no exista detalle de deducción. Esa evidencia quedará identificada como inferida. Las partidas reales conservarán sus obligaciones de seguimiento."))}</p>
                <p><b>${esc(__("Base / depósito"))}: ${amount(plan.total_usd)}</b> · ${esc(__("Aplicado"))}: ${amount(plan.applied_usd)} · ${esc(__("Ajustes"))}: ${amount(plan.adjustment_usd)}</p>
                <div class="table-responsive" style="max-height:45vh;overflow:auto"><table class="table table-bordered">
                <thead><tr>${["Período", "Cliente", "Crédito", "Base", "Base US$", "Aplicado US$", "Ajuste US$", "Clasificación"].map(x => `<th>${esc(__(x))}</th>`).join("")}</tr></thead>
                <tbody>${plan.rows.map(row => {
                    const adjustment = plan.adjustments.find(x => x.collection_row === row.collection_row);
                    return `<tr><td>${esc(row.period)}</td><td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(row.basis)}</td>
                    <td class="text-right">${amount(row.base_usd)}</td><td class="text-right">${amount(row.applied_usd)}</td><td class="text-right">${amount(row.amount_usd)}</td><td>${esc(adjustment?.category || "Sin diferencia")}</td></tr>`;
                }).join("")}</tbody></table></div>`},
            {fieldname: "confirm_correspondence", fieldtype: "Check", reqd: 1,
                label: __("Confirmo que este depósito corresponde a las cobranzas/deducciones mostradas, no solo que el monto coincide")},
        ],
        primary_action_label: __("Crear partidas y conciliar este depósito"),
        async primary_action(values) {
            if (dialog.transfer_in_progress || !values.confirm_correspondence) return;
            dialog.transfer_in_progress = true;
            dialog.disable_primary_action();
            try {
                const result = await frappe.call({
                    method: "credinomina_reconciliation.provisional_adjustments.apply_transfer",
                    args: {remittance_name: frm.doc.name, fingerprint: plan.fingerprint,
                        confirm_correspondence: values.confirm_correspondence}, freeze: true,
                    freeze_message: __("Creando partidas y aplicando la distribución…"),
                });
                dialog.hide();
                await frm.reload_doc();
                frappe.msgprint(__("Resultado: {0}. Revise las partidas vinculadas para su seguimiento.", [result.message.result]));
            } finally { dialog.transfer_in_progress = false; dialog.enable_primary_action(); }
        },
    });
    dialog.show();
}

async function correctDepositData(frm) {
    if (frm.is_dirty()) await frm.save();
    const modified = frm.doc.modified;
    const dialog = new frappe.ui.Dialog({
        title: __("Corregir datos del depósito"),
        fields: [
            {fieldname: "employer", fieldtype: "Link", options: "CN Employer", label: __("Empresa pagadora"), reqd: 1, default: frm.doc.employer},
            {fieldname: "deposit_date", fieldtype: "Date", label: __("Fecha del depósito"), reqd: 1, default: frm.doc.deposit_date},
            {fieldname: "deposit_reference", fieldtype: "Data", label: __("Referencia del depósito"), reqd: 1, default: frm.doc.deposit_reference},
            {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo de la corrección"), reqd: 1,
                description: __("Los cambios quedan registrados. Use Conciliar después de corregir los datos.")},
        ],
        primary_action_label: __("Guardar corrección"),
        async primary_action(values) {
            dialog.disable_primary_action();
            try {
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.correct_deposit_data",
                    args: {remittance_name: frm.doc.name, modified, ...values},
                    freeze: true, freeze_message: __("Guardando corrección…"),
                });
                dialog.hide();
                await frm.reload_doc();
            } finally {
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();
}

async function reconcileRemittance(frm) {
    if (frm.reconciliation_running) return;
    frm.reconciliation_running = true;
    const name = frm.doc.name;
    const progressId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const event = "cn_remittance_reconciliation_progress";
    const onProgress = data => {
        if (data.remittance_name !== name || data.progress_id !== progressId) return;
        // The percentages identify stages, not an estimated remaining duration.
        $("#freeze .freeze-message").text(`${__("Conciliando")}: ${data.percent}% — ${__(data.message)}`);
    };
    try {
        if (frm.is_dirty()) await frm.save();
        let reason = "";
        if (Number(frm.doc.allocated_usd) > 0 || Number(frm.doc.justified_surplus_usd) > 0) {
            reason = await new Promise(resolve => {
                let accepted = false;
                const dialog = new frappe.ui.Dialog({title: __("Verificar distribución existente"),
                    fields: [{fieldname: "reason", fieldtype: "Small Text", label: __("Motivo"), reqd: 1,
                        description: __("Explique qué está verificando o corrigiendo. Si cambia la distribución, se conservarán el antes y el después.")}],
                    primary_action_label: __("Conciliar"),
                    primary_action(values) { accepted = true; resolve(values.reason); dialog.hide(); },
                    onhide() { if (!accepted) resolve(null); }});
                dialog.show();
            });
            if (reason === null) return;
        }
        frappe.realtime.on(event, onProgress);
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.reconcile_remittance",
            args: { remittance_name: name, progress_id: progressId, reason },
            freeze: true,
            freeze_message: __("Conciliando este depósito y actualizando sus destinos…"),
        });
        await frm.reload_doc();
        const result = response.message || {};
        const escape = value => frappe.utils.escape_html(String(value ?? ""));
        frappe.msgprint({
            title: __("Conciliación finalizada"),
            indicator: remittancePresentation(frm.doc).color,
            message: `<p><b>${__("Estado del depósito")}:</b> ${escape(frm.doc.result)}</p>
                <p>${__("Depósito")}: ${escape(result.deposit || name)}</p>
                <p>${__("Filas del detalle")}: ${escape(result.detail_rows || 0)} ·
                ${__("Conciliadas")}: ${escape(result.detail_matched || 0)} ·
                ${__("Pendientes")}: ${escape(result.detail_pending || 0)}</p>
                <p>${__("Destinos")}: ${escape(result.targets || 0)}</p>
                <p>${__("Períodos afectados")}: ${escape((result.periods || []).join(", ") || "—")}</p>`,
        });
    } finally {
        frappe.realtime.off(event, onProgress);
        frm.reconciliation_running = false;
    }
}

async function showReconciliationHistory(frm) {
    const escape = value => frappe.utils.escape_html(String(value ?? ""));
    const labels = {amount_usd: "Depositado US$", allocated_usd: "Distribuido US$",
        justified_surplus_usd: "Saldo a favor documentado US$", unclassified_usd: "Sin clasificar US$",
        result: "Resultado", detail_status: "Estado del detalle"};
    const dialog = new frappe.ui.Dialog({title: __("Historial de conciliación"), size: "extra-large",
        fields: [{fieldname: "history", fieldtype: "HTML"}], primary_action_label: __("Cerrar"), primary_action: () => dialog.hide()});
    const wrapper = dialog.get_field("history").$wrapper;
    let start = 0, loading = false;
    function distribution(value) {
        let rows;
        try { rows = JSON.parse(value || "[]"); } catch (_) { return `<pre>${escape(value)}</pre>`; }
        if (!Array.isArray(rows)) return `<pre>${escape(value)}</pre>`;
        return `<table class="table table-bordered"><thead><tr><th>${__("Destino")}</th><th>${__("Período / documento")}</th><th>${__("US$")}</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escape(row.tipo)}</td><td>${escape([row.periodo, row.aplicacion_id || row.partida || row.fila_id, row.credito].filter(Boolean).join(" · "))}</td><td class="text-right">${escape(format_currency(row.importe_usd, "USD", 2))}</td></tr>`).join("") || `<tr><td colspan="3">${__("Sin distribución")}</td></tr>`}</tbody></table>`;
    }
    async function load() {
        if (loading) return;
        loading = true;
        try {
            const {message} = await frappe.call({method: "credinomina_reconciliation.reconciliation_audit.get_history",
                args: {deposit_name: frm.doc.name, start}, freeze: true});
            wrapper.find("[data-more-history]").remove();
            if (!start) wrapper.html(`<p class="text-muted">${escape(message.notice)}</p>`);
            for (const row of message.rows || []) {
                const before = row.before || {}, after = row.after || {};
                wrapper.append(`<details style="margin-bottom:16px"><summary><strong>${escape(frappe.datetime.str_to_user(row.date))}</strong> · ${escape(row.user)} · ${escape(row.action)}</summary>
                    <p>${escape(row.reason || row.command || __("Actualización automática"))}</p>
                    <table class="table table-bordered"><thead><tr><th>${__("Dato")}</th><th>${__("Antes")}</th><th>${__("Después")}</th></tr></thead><tbody>${Object.entries(labels).map(([key, label]) => `<tr><td>${escape(__(label))}</td><td>${escape(key.endsWith("_usd") ? format_currency(before[key], "USD", 2) : before[key])}</td><td>${escape(key.endsWith("_usd") ? format_currency(after[key], "USD", 2) : after[key])}</td></tr>`).join("")}</tbody></table>
                    <h5>${__("Distribución anterior")}</h5>${distribution(before.allocation_detail)}
                    <h5>${__("Nueva distribución")}</h5>${distribution(after.allocation_detail)}</details>`);
            }
            if (!start && !message.rows?.length) wrapper.append(`<p>${__("Aún no hay cambios de conciliación registrados en esta bitácora.")}</p>`);
            start = message.next_start;
            if (message.has_more) wrapper.append(`<button type="button" class="btn btn-default" data-more-history>${__("Ver anteriores")}</button>`);
        } finally { loading = false; }
    }
    wrapper.on("click", "[data-more-history]", load);
    dialog.show();
    await load();
}

function toggleApplicationDetailAction(frm) {
    frm.toggle_display("use_applications_detail", (frm.doc.detail_periods || []).some(row => row.period) &&
        frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
}

frappe.ui.form.on("CN Remittance Period", {
    period(frm) { toggleApplicationDetailAction(frm); },
    applied_usd(frm) {
        const cents = (frm.doc.detail_periods || []).reduce((total, row) =>
            total + Math.round(Number(row.applied_usd || 0) * 100), 0);
        frm.set_value("applied_usd", cents / 100);
    },
    detail_periods_add(frm) { toggleApplicationDetailAction(frm); },
    detail_periods_remove(frm) {
        const cents = (frm.doc.detail_periods || []).reduce((total, row) =>
            total + Math.round(Number(row.applied_usd || 0) * 100), 0);
        frm.set_value("applied_usd", cents / 100);
        toggleApplicationDetailAction(frm);
    },
});

async function useApplicationsAsDetail(frm) {
    if (!(frm.doc.detail_periods || []).some(row => row.period)) {
        frappe.msgprint(__("Seleccione al menos un período del detalle."));
        return;
    }
    if (frm.is_new() || frm.is_dirty()) await frm.save();
    const method = "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.";
    const response = await frappe.call({method: method + "preview_application_detail",
        args: {remittance_name: frm.doc.name}, freeze: true,
        freeze_message: __("Consultando aplicaciones pendientes de los períodos…")});
    const preview = response.message;
    if (!preview.rows.length) {
        frappe.msgprint(__("No hay aplicaciones pendientes para los períodos seleccionados."));
        return;
    }
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const selected = new Set(preview.rows.map(row => row.claim_id));
    const amounts = new Map(preview.rows.map(row => [row.claim_id, Number(row.deducted_usd).toFixed(2)]));
    function amountCents(row) {
        const value = amounts.get(row.claim_id).trim();
        const cents = Math.round(Number(value) * 100);
        return /^\d+(?:\.\d{1,2})?$/.test(value) && Number.isSafeInteger(cents) && cents > 0 ? cents : null;
    }
    const invalidAmounts = () => preview.rows.some(row => selected.has(row.claim_id) && amountCents(row) === null);
    let busy = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Usar aplicaciones pendientes como detalle"), size: "extra-large",
        fields: [{fieldname: "selection_preview", fieldtype: "HTML", options: `
            <p>${__("Se generará un archivo privado con las aplicaciones pendientes de los períodos {0}, descontando lo cubierto por otros depósitos. Las asignaciones de este depósito se conservan para poder completar su propio detalle.", [esc(preview.periods.join(", "))])}</p>
            <p><strong>${__("Aplicado a los períodos")}: US$ ${remittanceMoney(preview.applied_usd)} ·
                ${__("Seleccionado")}: US$ <span data-selected-total>${remittanceMoney(preview.total_usd)}</span> ·
                ${__("Depósito")}: US$ ${remittanceMoney(preview.deposit_usd)}</strong></p>
            <p class="text-warning" data-selection-warning>${__("El total seleccionado no coincide con el depósito. No se repartirán ni reducirán importes automáticamente; deberá revisar la diferencia y los destinos antes de conciliar.")}</p>
            <p>${__("Este detalle procede del core, no de una confirmación de deducción de la empresa. Generarlo no confirma ni concilia el depósito.")}</p>
            <p>${__("Marque los movimientos de este depósito y ajuste sus importes si son distintos. El pendiente original se muestra como referencia; los ajustes se registrarán en los comentarios del detalle.")}</p>
            <p class="text-danger" data-amount-error>${__("Ingrese importes mayores que cero con un máximo de dos decimales en las filas seleccionadas.")}</p>
            <p data-selection-count aria-live="polite"></p>
            <div style="max-height:40vh;overflow:auto"><table class="table table-bordered"><thead>
                <tr><th><label><input type="checkbox" data-select-all checked> ${__("Todos")}</label></th><th>${__("Cliente")}</th><th>${__("Crédito")}</th><th>${__("Período")}</th><th>${__("Referencia")}</th><th>${__("Importe del detalle US$")}</th></tr></thead>
                <tbody>${preview.rows.map((row, index) => `<tr><td><input type="checkbox" data-application-index="${index}" checked aria-label="${esc(__("Seleccionar movimiento {0}", [index + 1]))}"></td><td>${esc(row.client_name)}<br>${esc(row.client_number)}</td>
                    <td>${esc(row.loan_number)}</td><td>${esc(row.period)}</td><td>${esc(row.application_reference)}</td><td>
                        <input type="number" class="form-control input-sm" style="min-width:120px" min="0.01" step="0.01" data-application-amount="${index}"
                            value="${esc(amounts.get(row.claim_id))}" aria-label="${esc(__("Importe del movimiento {0} en US$", [index + 1]))}">
                        <small class="text-muted">${__("Pendiente original")}: ${remittanceMoney(row.deducted_usd)}</small></td></tr>`).join("")}</tbody></table></div>
            ${preview.replaces_detail ? `<p class="text-warning">${__("Se reemplazarán las filas del detalle actual y se quitarán sus vínculos a destinos. Los destinos y archivos anteriores se conservarán para revisión.")}</p>` : ""}
        `}, ...(preview.replaces_detail ? [{fieldname: "replace_detail", fieldtype: "Check", reqd: 1,
            label: __("Confirmo reemplazar el detalle actual")}] : [])],
        primary_action_label: __("Generar detalle pendiente de conciliación"),
        primary_action: async values => {
            if (busy) return;
            if (!selected.size) {
                frappe.msgprint(__("Seleccione al menos un movimiento para generar el detalle."));
                return;
            }
            if (invalidAmounts()) {
                frappe.msgprint(__("Revise los importes de las filas seleccionadas."));
                return;
            }
            busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                await frappe.call({method: method + "use_application_detail",
                    args: {remittance_name: frm.doc.name, fingerprint: preview.fingerprint,
                        replace_detail: values.replace_detail || 0,
                        selected_claim_ids: JSON.stringify([...selected]),
                        selected_amounts: JSON.stringify(Object.fromEntries([...selected].map(key => [key, amounts.get(key)])))}, freeze: true,
                    freeze_message: __("Generando detalle desde aplicaciones…")});
                dialog.hide();
                await frm.reload_doc();
                frappe.msgprint(__("Detalle generado. Revise las filas y los destinos; luego confirme el depósito si es borrador y use Conciliar."));
            } finally {
                busy = false;
                updateSelection();
            }
        },
    });
    const wrapper = dialog.fields_dict.selection_preview.$wrapper;
    function updateSelection() {
        const invalid = invalidAmounts();
        const cents = preview.rows.filter(row => selected.has(row.claim_id))
            .reduce((sum, row) => sum + (amountCents(row) || 0), 0);
        wrapper.find("[data-selected-total]").text(invalid ? "—" : remittanceMoney(cents / 100));
        wrapper.find("[data-amount-error]").toggle(invalid);
        wrapper.find("[data-selection-count]").text(__("{0} de {1} movimientos seleccionados", [selected.size, preview.rows.length]));
        wrapper.find("[data-selection-warning]").toggle(!invalid && cents !== Math.round(preview.deposit_usd * 100));
        wrapper.find("[data-select-all]").prop("checked", selected.size === preview.rows.length)
            .prop("indeterminate", selected.size > 0 && selected.size < preview.rows.length);
        preview.rows.forEach((row, index) => wrapper.find(`[data-application-amount="${index}"]`)
            .prop("disabled", busy || !selected.has(row.claim_id)));
        dialog.get_primary_btn().prop("disabled", busy || !selected.size || invalid);
    }
    wrapper.on("input change", "[data-application-amount]", event => {
        const row = preview.rows[Number(event.target.dataset.applicationAmount)];
        amounts.set(row.claim_id, event.target.value);
        updateSelection();
    });
    wrapper.on("change", "[data-application-index]", event => {
        const row = preview.rows[Number(event.target.dataset.applicationIndex)];
        if (event.target.checked) selected.add(row.claim_id);
        else selected.delete(row.claim_id);
        updateSelection();
    });
    wrapper.on("change", "[data-select-all]", event => {
        selected.clear();
        if (event.target.checked) preview.rows.forEach(row => selected.add(row.claim_id));
        wrapper.find("[data-application-index]").prop("checked", event.target.checked);
        updateSelection();
    });
    dialog.show();
    updateSelection();
}

frappe.ui.form.on("CN Remittance Detail", {
    create_client_credit: createClientCreditFromDetail,
    create_company_credit: createCompanyCreditFromDetail,
    select_pending_targets: selectPendingTargetsForDetail,
    form_render(frm, cdt, cdn) {
        const row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
        const control = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.create_client_credit;
        control?.$wrapper.toggle(canCreateDetailClientCredit(frm, row));
        const companyControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.create_company_credit;
        companyControl?.$wrapper.toggle(canCreateDetailClientCredit(frm, row) && canCreateCompanyCredit(frm));
        const targetControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.select_pending_targets;
        targetControl?.$wrapper.toggle(canSelectDetailPendingTargets(frm, row));
    },
    loan_number(frm) {
        frm.set_value("result", "Pendiente");
        frm.set_value("detail_status", "Cargado; pendiente de conciliación");
        frappe.show_alert({message: __("Guarde y use Conciliar. Revise los destinos manuales si cambió el crédito."), indicator: "orange"});
    },
});

function canSelectDetailPendingTargets(frm, row) {
    const identity = row && [row.client, row.client_number, row.employee_number, row.national_id,
        row.client_name, row.loan_number].some(value => String(value || "").trim());
    return !frm.is_new() && frm.doc.docstatus === 1 && !!frm.get_perm(0, "write") &&
        !!row?.name && identity && Number(row.pending_usd) > 0;
}

async function selectPendingTargetsForDetail(frm, cdt, cdn) {
    if (frm.detail_target_workflow) return;
    if (frm.detail_target_picker) {
        frappe.show_alert({message: __("Ya hay un selector de partidas abierto. Ciérrelo antes de abrir otro."), indicator: "orange"});
        return;
    }
    let row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
    if (!canSelectDetailPendingTargets(frm, row)) {
        frappe.msgprint(__("Seleccione una fila identificable con importe pendiente de un depósito confirmado."));
        return;
    }
    if (frm.is_dirty()) {
        const save = await new Promise(resolve => frappe.confirm(
            __("Se guardarán los cambios del depósito antes de consultar las partidas de este cliente. Esto no concilia el depósito. ¿Continuar?"),
            () => resolve(true), () => resolve(false)
        ));
        if (!save) return;
        await frm.save();
        row = (frm.doc.detail_rows || []).find(item => item.name === cdn);
    }
    if (!canSelectDetailPendingTargets(frm, row)) {
        frappe.msgprint(__("La fila cambió al guardar. Revísela y vuelva a abrir el selector."));
        return;
    }
    frm.detail_target_workflow = true;
    try {
        const data = await loadPendingRemittanceTargets(frm, row.name);
        const picker = new RemittanceTargetPicker(frm, data, row);
        frm.detail_target_picker = picker;
    } finally {
        frm.detail_target_workflow = false;
    }
}

function canCreateDetailClientCredit(frm, row) {
    const pending = Number(row?.pending_usd);
    return !frm.is_new() && frm.doc.docstatus === 1 && !!frm.get_perm(0, "write") &&
        !!frappe.model.can_create("CN Complementary Item") && !!frappe.model.can_submit("CN Complementary Item") &&
        !!row && Number.isFinite(pending) && pending > 0;
}

function addBulkClientCreditButton(frm) {
    const grid = frm.fields_dict.detail_rows?.grid;
    // Frappe creates these controls when the grid renders, after form setup.
    if (!grid?.add_custom_button || !grid.get_selected_children || !grid.custom_buttons
        || !grid.grid_buttons || !grid.wrapper) return;
    const label = __("Crear saldos a favor seleccionados");
    frm.bulkClientCreditButton = grid.add_custom_button(label, () => createBulkClientCredits(frm), "bottom");
    const refresh = () => {
        const selected = grid.get_selected_children();
        const available = selected.length >= 2 && selected.every(row => canCreateDetailClientCredit(frm, row));
        frm.bulkClientCreditButton.toggleClass("hidden", !available);
    };
    grid.wrapper.off("change.cnBulkClientCredits", ".grid-row-check")
        .on("change.cnBulkClientCredits", ".grid-row-check", () => setTimeout(refresh, 0));
    refresh();
}

async function createBulkClientCredits(frm) {
    if (frm.bulk_detail_credit_workflow || frm.detail_credit_workflow || frm.company_credit_workflow) return;
    const grid = frm.fields_dict.detail_rows?.grid;
    const selectedNames = (grid?.get_selected_children?.() || []).map(row => row.name);
    if (selectedNames.length < 2) {
        frappe.msgprint(__("Seleccione al menos dos filas con importe pendiente."));
        return;
    }
    frm.bulk_detail_credit_workflow = true;
    let dialogOpened = false;
    try {
        if (frm.is_dirty()) await frm.save();
        const rows = selectedNames.map(name => (frm.doc.detail_rows || []).find(row => row.name === name));
        if (rows.some(row => !canCreateDetailClientCredit(frm, row))) {
            frappe.msgprint(__("Una o más filas cambiaron o ya no tienen pendiente. Recargue el depósito y revise la selección."));
            return;
        }
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        const dialog = new frappe.ui.Dialog({
            title: __("Crear saldos a favor del cliente"), size: "large",
            onhide() { frm.bulk_detail_credit_workflow = false; },
            fields: [
                {fieldname: "rows_preview", fieldtype: "HTML", options: `<div class="alert alert-warning">${esc(__("Se creará una partida independiente por cada fila. El motivo y el comentario se compartirán; revise el importe individual de cada saldo. No se aplica a los créditos."))}</div>
                    <div class="table-responsive"><table class="table table-bordered"><thead><tr><th>${esc(__("Fila"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Pendiente US$"))}</th><th>${esc(__("Saldo a favor US$"))}</th></tr></thead>
                    <tbody>${rows.map(row => `<tr><td>${esc(row.source_row || row.idx)}</td>
                    <td>${esc(row.client_name || row.client_number || __("Cliente sin identificar"))}<div class="text-muted">${esc(row.loan_number || "")}</div></td>
                    <td class="text-right">${esc(remittanceMoney(row.pending_usd))}</td>
                    <td><input class="form-control input-sm" type="number" min="0.01" step="0.01" max="${esc(row.pending_usd)}"
                        data-credit-amount="${esc(row.name)}" value="${Number(row.pending_usd).toFixed(2)}" required></td></tr>`).join("")}</tbody></table></div>`},
                {fieldname: "reason_type", fieldtype: "Select", label: __("Motivo del saldo a favor"),
                    options: "\nError de la empresa\nPago adicional no informado\nPor refinanciamiento\nPor cancelación\nOtro por aclarar", reqd: 1},
                {fieldname: "credit_treatment", fieldtype: "Select", label: __("Tratamiento"),
                    options: "Pendiente de decisión\nDevolución\nAplicación futura", default: "Pendiente de decisión", reqd: 1},
                {fieldname: "credit_assigned_to", fieldtype: "Link", options: "User", label: __("Responsable"), default: frappe.session?.user, reqd: 1},
                {fieldname: "credit_commitment_date", fieldtype: "Date", label: __("Fecha compromiso"), reqd: 1},
                {fieldname: "posting_date", fieldtype: "Date", label: __("Fecha de la partida"), default: frm.doc.deposit_date, reqd: 1},
                {fieldname: "description", fieldtype: "Small Text", label: __("Comentario / justificación"), reqd: 1},
            ],
            primary_action_label: __("Crear y confirmar {0} saldos", [rows.length]),
            async primary_action(values) {
                if (dialog.running) return;
                const amounts = rows.map(row => {
                    const raw = dialog.fields_dict.rows_preview.$wrapper.find(`[data-credit-amount="${row.name}"]`).val();
                    const amount = Number(raw);
                    return {detail_row: row.name, amount,
                        valid: /^\d+(?:\.\d{1,2})?$/.test(String(raw || "")) && Number.isFinite(amount)
                            && amount > 0 && amount <= Number(row.pending_usd)};
                });
                if (amounts.some(row => !row.valid)) {
                    frappe.msgprint(__("Cada importe debe ser positivo y no superar el pendiente de su fila."));
                    return;
                }
                dialog.running = true;
                dialog.disable_primary_action();
                try {
                    const response = await frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.create_client_credit_items_bulk",
                        args: {remittance_name: frm.doc.name, modified: frm.doc.modified,
                            rows: JSON.stringify(amounts.map(({detail_row, amount}) => ({detail_row, amount}))), ...values},
                        freeze: true, freeze_message: __("Creando saldos a favor independientes…"),
                    });
                    dialog.hide();
                    await frm.reload_doc();
                    frappe.show_alert({message: __("Se crearon {0} saldos a favor independientes.",
                        [response.message.created.length]), indicator: "green"});
                } finally {
                    dialog.running = false;
                    dialog.enable_primary_action();
                }
            },
        });
        dialog.show();
        dialogOpened = true;
    } finally {
        if (!dialogOpened) frm.bulk_detail_credit_workflow = false;
    }
}

async function createClientCreditFromDetail(frm, cdt, cdn) {
    if (frm.detail_credit_workflow) return;
    const row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
    if (!canCreateDetailClientCredit(frm, row)) {
        frappe.msgprint(__("Seleccione una fila con pendiente positivo de un depósito confirmado. Necesita permisos para crear y confirmar partidas complementarias."));
        return;
    }
    frm.detail_credit_workflow = true;
    try {
        await createRemittanceComplementary(frm, {detailRowName: row.name});
    } catch (error) {
        frm.detail_credit_workflow = false;
        throw error;
    }
}

async function createCompanyCreditFromDetail(frm, cdt, cdn) {
    if (frm.company_credit_workflow || frm.detail_credit_workflow) return;
    const row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
    if (!canCreateDetailClientCredit(frm, row) || !canCreateCompanyCredit(frm)) return;
    frm.company_credit_workflow = true;
    try {
        return await createRemittanceComplementary(frm, {companyCredit: true, detailRowName: row.name});
    } catch (error) {
        frm.company_credit_workflow = false;
        throw error;
    }
}

function canCreateCompanyCredit(frm) {
    const pending = Number(frm.doc.unclassified_usd);
    return !frm.is_new() && frm.doc.docstatus === 1 && Number.isFinite(pending) && pending > 0 &&
        !!frm.get_perm(0, "write") && !!frappe.model.can_create("CN Complementary Item") &&
        !!frappe.model.can_submit("CN Complementary Item");
}

function addCompanyCreditButton(frm) {
    if (canCreateCompanyCredit(frm)) {
        frm.add_custom_button(__("Crear saldo a favor de la empresa"), () => createCompanyCreditFromDeposit(frm));
    }
}

async function createCompanyCreditFromDeposit(frm) {
    if (frm.company_credit_workflow) return;
    if (!canCreateCompanyCredit(frm)) {
        frappe.msgprint(__("Requiere un depósito confirmado con saldo sin clasificar y permisos para crear y confirmar partidas complementarias."));
        return;
    }
    frm.company_credit_workflow = true;
    try {
        return await createRemittanceComplementary(frm, {companyCredit: true});
    } catch (error) {
        frm.company_credit_workflow = false;
        throw error;
    }
}

async function createRemittanceComplementary(frm, options = {}) {
    if (frm.is_new() || frm.is_dirty()) await frm.save();
    const companyCredit = options.companyCredit === true;
    if (companyCredit && !canCreateCompanyCredit(frm)) {
        frm.company_credit_workflow = false;
        frappe.msgprint(__("El depósito cambió o ya no tiene saldo sin clasificar. Revise la distribución antes de crear el saldo a favor."));
        return;
    }
    const companyPending = companyCredit ? Number(frm.doc.unclassified_usd) : 0;
    const depositName = frm.doc.name;
    // Resolve the saved child again; saving may update its pending amount/identity.
    const sourceRow = options.detailRowName ? (frm.doc.detail_rows || []).find(row => row.name === options.detailRowName) : null;
    if (options.detailRowName && !canCreateDetailClientCredit(frm, sourceRow)) {
        frm.detail_credit_workflow = false;
        if (companyCredit) frm.company_credit_workflow = false;
        frappe.msgprint(__("La fila cambió o ya no tiene importe pendiente. Revise el detalle antes de registrar un saldo a favor."));
        return;
    }
    const escape = value => frappe.utils.escape_html(String(value ?? ""));
    let busy = false;
    const dialog = new frappe.ui.Dialog({
        title: __(companyCredit ? "Crear saldo a favor de la empresa" : sourceRow ? "Crear saldo a favor del cliente" : "Crear partida complementaria"), size: "large",
        onhide() {
            if (sourceRow) frm.detail_credit_workflow = false;
            if (companyCredit) frm.company_credit_workflow = false;
        },
        fields: [
            {fieldtype: "HTML", options: companyCredit ? `<p><strong>${escape(frm.doc.employer)}</strong> · ${escape(depositName)}</p>
                <p>${sourceRow ? `${escape(sourceRow.client_name || "")} · ${escape(sourceRow.loan_number || "")} · ${escape(__("Fila"))} ${escape(sourceRow.source_row || sourceRow.idx)}<br>${__("La partida quedará vinculada a esta fila como evidencia del excedente. Su beneficiaria es la empresa; reducirá el pendiente de la fila sin aumentar el pago al crédito del cliente.")}` : __("Se sugiere el saldo sin clasificar del depósito. Confirme que pertenece a la empresa y no a un cliente. No se aplica al crédito ni se agrega a Destinos; aparecerá en la distribución completa del depósito.")}</p>
                <p>${__("Si ya existe una partida importada de contabilidad para este saldo, vincule esa partida en lugar de crear otra. Registre justificación, responsable y fecha compromiso para su seguimiento.")}</p>` : sourceRow ? `<p><strong>${escape(sourceRow.client_name || sourceRow.client_number || "Cliente sin identificar")}</strong> · ${escape(sourceRow.loan_number || "Sin crédito")} · ${escape(__("Fila"))} ${escape(sourceRow.source_row || sourceRow.idx)}</p>
                <p>${__("Se vinculará el saldo a favor a esta fila y al depósito. El importe pendiente se sugiere en US$; revíselo y confirme que sea un excedente del cliente, no una aplicación que falte identificar. No aumenta lo aplicado al crédito.")}</p>
                <p>${__("Si el saldo ya fue importado de contabilidad, reclasifique esa partida existente para no duplicar su registro.")}</p>` : `<p>Use un importe positivo para un depósito mayor que la aplicación y negativo cuando falta depósito.
                Por ejemplo: aplicado US$90, depósito US$100 → +US$10; aplicado US$100, depósito US$90 → −US$10.
                  Si usa un importe negativo, agréguelo antes de seleccionar las aplicaciones restantes.
                  Para dinero sin aplicación ni ingreso identificado, elija <b>Saldo a favor de la empresa</b>:
                  debe ser positivo y no se agrega al detalle por cliente ni a Destinos.
                  Si el excedente pertenece a una persona, use <b>Saldo a favor del cliente</b>, identifique la fila y registre responsable y fecha compromiso. No aumenta lo aplicado al crédito.</p>`},
            {fieldname: "category", fieldtype: "Select", label: __("Concepto"), options: "Cobranza administrativa\nOtros ingresos\nAjuste de conciliación\nCuenta por Cobrar a la Empresa\nSaldo a favor de la empresa\nSaldo a favor del cliente", default: companyCredit ? "Saldo a favor de la empresa" : sourceRow ? "Saldo a favor del cliente" : "Ajuste de conciliación", read_only: !!sourceRow || companyCredit, reqd: 1},
            {fieldname: "receivable_help", fieldtype: "HTML", depends_on: "eval:doc.category === 'Cuenta por Cobrar a la Empresa'",
                options: `<p>${__("Registre el faltante del depósito con importe negativo. Por ejemplo, aplicado US$100 y depositado US$90: ingrese −10. La deuda de la empresa se reconocerá al conciliar la distribución y permanecerá pendiente hasta registrar su cobro o compensación.")}</p>`},
            {fieldname: "subcategory", fieldtype: "Link", options: "CN Complementary Subcategory", label: __("Subcategoría"), depends_on: "eval:doc.category === 'Ajuste de conciliación'", mandatory_depends_on: "eval:doc.category === 'Ajuste de conciliación'", description: __("Indique si el faltante queda como CxC a la empresa o corresponde a otro ajuste.")},
            {fieldname: "reason_type", fieldtype: "Select", label: __("Motivo del saldo a favor"),
                options: "\nError de la empresa\nPago adicional no informado\nPor refinanciamiento\nPor cancelación\nOtro por aclarar", default: "",
                depends_on: "eval:['Saldo a favor del cliente', 'Saldo a favor de la empresa'].includes(doc.category)",
                mandatory_depends_on: "eval:['Saldo a favor del cliente', 'Saldo a favor de la empresa'].includes(doc.category)",
                description: __("Seleccione el motivo. Puede corregirlo posteriormente en la partida, incluso después de confirmarla.")},
            {fieldname: "posting_date", fieldtype: "Date", label: __("Fecha de la partida"), default: frm.doc.deposit_date, reqd: 1},
            {fieldtype: "Column Break"},
            {fieldname: "currency", fieldtype: "Select", label: __("Moneda"), options: "USD\nNIO", default: "USD", read_only: !!sourceRow || companyCredit, reqd: 1},
            {fieldname: "amount", fieldtype: "Currency", options: "currency", label: __(companyCredit ? "Saldo a favor US$" : "Importe (+ / −)"), precision: 2, reqd: 1},
            {fieldname: "fx_rate", fieldtype: "Float", label: __("Tipo de cambio C$/US$"), precision: 8, default: frm.doc.fx_rate,
                depends_on: "eval:doc.currency === 'NIO'", mandatory_depends_on: "eval:doc.currency === 'NIO'"},
            {fieldtype: "Section Break", label: __("Seguimiento del saldo a favor"), depends_on: "eval:['Saldo a favor del cliente', 'Saldo a favor de la empresa'].includes(doc.category)"},
            {fieldname: "credit_detail_row", fieldtype: "Select", label: __("Fila que incluye el excedente (opcional)"),
                depends_on: "eval:doc.category === 'Saldo a favor del cliente'",
                read_only: !!sourceRow,
                description: __("Si detalle US$110 incluye US$10 de exceso, vincule su fila. Si el detalle muestra solo los US$100 aplicados, deje el vínculo vacío."),
                options: [{value: "", label: "Excedente fuera del detalle por cliente"}, ...(frm.doc.detail_rows || []).map(row => ({value: row.name, label: `${row.client_name || row.client_number || "Cliente sin identificar"} · ${row.loan_number || "Sin crédito"}`}))],
                async onchange() {
                    const row = (frm.doc.detail_rows || []).find(row => row.name === dialog.get_value("credit_detail_row"));
                    if (row) await dialog.set_values({credit_client: row.client || "", client_number: row.client_number || "", loan_number: row.loan_number || ""});
                }},
            {fieldname: "credit_client", fieldtype: "Link", options: "CN Client", label: __("Cliente beneficiario"),
                depends_on: "eval:doc.category === 'Saldo a favor del cliente'",
                read_only: !!sourceRow?.client,
                mandatory_depends_on: "eval:doc.category === 'Saldo a favor del cliente'",
                get_query: () => ({filters: {employer: ["in", frm.paying_companies || [frm.doc.employer]]}})},
            {fieldname: "credit_treatment", fieldtype: "Select", label: __("Tratamiento"), options: "Pendiente de decisión\nDevolución\nAplicación futura", default: "Pendiente de decisión"},
            {fieldtype: "Column Break"},
            {fieldname: "credit_assigned_to", fieldtype: "Link", options: "User", label: __("Responsable"), default: frappe.session?.user,
                mandatory_depends_on: "eval:['Saldo a favor del cliente', 'Saldo a favor de la empresa'].includes(doc.category)"},
            {fieldname: "credit_commitment_date", fieldtype: "Date", label: __("Fecha compromiso"), mandatory_depends_on: "eval:['Saldo a favor del cliente', 'Saldo a favor de la empresa'].includes(doc.category)"},
            {fieldtype: "Section Break", label: __("Identificación y seguimiento")},
            {fieldname: "description", fieldtype: "Small Text", label: __("Justificación"), reqd: 1},
            {fieldname: "voucher", fieldtype: "Data", label: __("Asiento contable (opcional)"), description: __("Sin asiento quedará pendiente de registro contable. Puede completarlo después en la partida.")},
            {fieldname: "voucher_line", fieldtype: "Data", label: __("Identificador de la partida en el asiento"),
                description: __("Ingrese el número o código de esta partida, por ejemplo 1, 2 o A. Registre cada partida del mismo asiento por separado con un identificador distinto.")},
            {fieldtype: "Section Break", label: __("Vínculo con cliente (opcional)"), collapsible: 1},
            {fieldname: "period", fieldtype: "Link", options: "CN Reconciliation Period", label: __("Período"),
                get_query: () => ({filters: {employer: ["in", frm.paying_companies || [frm.doc.employer]], status: ["!=", "Cerrado"]}})},
            {fieldname: "client_number", fieldtype: "Data", label: __("Nro. Cliente"), read_only: !!sourceRow, depends_on: "eval:doc.category !== 'Saldo a favor de la empresa' || !!doc.client_number"},
            {fieldtype: "Column Break"},
            {fieldname: "loan_number", fieldtype: "Data", label: __("Nro. Crédito"), read_only: !!sourceRow, depends_on: "eval:doc.category !== 'Saldo a favor de la empresa' || !!doc.loan_number"},
            {fieldname: "installment_number", fieldtype: "Data", label: __("Nro. Cuota"), depends_on: "eval:doc.category !== 'Saldo a favor de la empresa' || !!doc.installment_number"},
        ],
        primary_action_label: __(sourceRow || companyCredit ? "Crear y confirmar saldo a favor" : "Crear y confirmar partida"),
        primary_action: async values => {
            if (busy) return;
            if (companyCredit) {
                if (frm.doc.name !== depositName || !canCreateCompanyCredit(frm)) {
                    frappe.msgprint(__("El depósito cambió. Cierre el modal y revise el registro antes de continuar."));
                    return;
                }
                const amount = Number(values.amount);
                if (!Number.isFinite(amount) || amount <= 0 || amount > companyPending) {
                    frappe.msgprint(__("Indique un importe positivo que no supere el saldo sin clasificar del depósito en US$."));
                    return;
                }
                values = {...values, category: "Saldo a favor de la empresa", currency: "USD",
                    credit_client: "", credit_detail_row: sourceRow?.name || "", client_number: "", loan_number: "", installment_number: ""};
            }
            if (sourceRow && (!Number.isFinite(Number(values.amount)) || Number(values.amount) <= 0 || Number(values.amount) > Number(sourceRow.pending_usd))) {
                frappe.msgprint(__("Indique un importe positivo que no supere el pendiente de la fila en US$."));
                return;
            }
            // The shortcut is explicitly bound to this saved child and customer.
            if (sourceRow && !companyCredit) values = {...values, category: "Saldo a favor del cliente", currency: "USD",
                credit_detail_row: sourceRow.name, credit_client: sourceRow.client || values.credit_client,
                client_number: sourceRow.client_number || "", loan_number: sourceRow.loan_number || ""};
            if (values.category === "Cuenta por Cobrar a la Empresa" &&
                (!Number.isFinite(Number(values.amount)) || Number(values.amount) >= 0)) {
                frappe.msgprint(__("Para Cuenta por Cobrar a la Empresa, ingrese el faltante como importe negativo."));
                return;
            }
            busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                const response = await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.create_complementary_item",
                    args: {remittance_name: frm.doc.name, modified: frm.doc.modified, values},
                    freeze: true, freeze_message: __("Creando partida complementaria…"),
                });
                dialog.hide();
                await frm.reload_doc();
                const message = response.message.company_credit || response.message.client_credit
                    ? __("Saldo a favor documentado y vinculado al depósito. No se aplicó a créditos.")
                    : response.message.company_receivable
                        ? __("Cuenta por cobrar agregada a Destinos. Complete la distribución y use Conciliar para reconocer el saldo por cobrar a la empresa.")
                        : __("Partida agregada a Destinos. Complete la distribución y use Conciliar.");
                frappe.msgprint({title: __("Partida registrada"), message: `${frappe.utils.escape_html(response.message.name)} · ${frappe.utils.escape_html(response.message.accounting_status)}.<br>${frappe.utils.escape_html(message)}`});
            } finally {
                busy = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    if (companyCredit) await dialog.set_values({category: "Saldo a favor de la empresa", currency: "USD",
        amount: Math.round(Math.min(companyPending, sourceRow ? Number(sourceRow.pending_usd) : companyPending) * 100) / 100,
        credit_detail_row: sourceRow?.name || "", reason_type: sourceRow ? "Error de la empresa" : ""});
    if (sourceRow && !companyCredit) await dialog.set_values({category: "Saldo a favor del cliente", currency: "USD",
        amount: Math.round(Number(sourceRow.pending_usd) * 100) / 100, credit_detail_row: sourceRow.name,
        credit_client: sourceRow.client || "", client_number: sourceRow.client_number || "", loan_number: sourceRow.loan_number || ""});
    dialog.show();
    return dialog;
}

async function linkRemittanceDetailTargets(frm) {
    if (frm.is_new() || frm.is_dirty()) await frm.save();
    const rows = frm.doc.detail_rows || [];
    const targets = frm.doc.targets || [];
    if (!rows.length || !targets.length) {
        frappe.msgprint(__("Cargue el detalle y agregue los destinos antes de vincularlos."));
        return;
    }
    const labels = new Map(rows.map(row => [
        `Fila ${row.source_row || row.idx} · ${row.client_name || "Sin nombre"} · ${row.loan_number || "Sin crédito"} · US$ ${remittanceMoney(row.deducted_usd)} / C$ ${remittanceMoney(row.deducted_nio)}`,
        row.name,
    ]));
    let dialog, busy = false;
    function loadTargets() {
        if (!dialog) return;
        const rowName = labels.get(dialog.get_value("detail"));
        const grid = dialog.fields_dict.destinations;
        grid.df.data = targets.filter(target => !target.detail_row || target.detail_row === rowName).map(target => ({
            target_id: target.name,
            linked: target.detail_row === rowName ? 1 : 0,
            destination: target.notes || target.historical_application || target.complementary_item ||
                `${target.period || ""} · ${target.row_key || ""}`,
            amount_usd: target.amount_usd,
        }));
        grid.grid.refresh();
    }
    dialog = new frappe.ui.Dialog({
        title: __("Vincular detalle y destinos"), size: "extra-large",
        fields: [
            {fieldname: "detail", fieldtype: "Select", label: __("Fila del detalle"),
                options: [...labels.keys()], onchange: loadTargets},
            {fieldname: "help", fieldtype: "HTML", options: `<p class="text-muted">Marque los destinos que cubren esta fila. Puede vincular varias aplicaciones a una fila; sus importes deben sumar el equivalente US$ de la fila.
                Para repartir una partida complementaria entre clientes, edite Importe a vincular: el restante queda como otro destino sin vincular, conservando el mismo total.
                Los destinos vinculados a otra fila no se muestran. Para liberar un destino, seleccione su fila, desmárquelo y guarde.
                Al cambiar de fila se descartan las marcas sin guardar. Volver a importar el archivo elimina estos vínculos.</p>`},
            {fieldname: "destinations", fieldtype: "Table", label: __("Destinos del depósito"),
                cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
                fields: [
                    {fieldname: "linked", fieldtype: "Check", label: __("Vincular"), in_list_view: 1, columns: 1},
                    {fieldname: "destination", fieldtype: "Small Text", label: __("Destino"), read_only: 1, in_list_view: 1, columns: 7},
                    {fieldname: "amount_usd", fieldtype: "Currency", label: __("Importe a vincular US$"), in_list_view: 1, columns: 2, precision: 2},
                    {fieldname: "target_id", fieldtype: "Data", hidden: 1},
                ], data: []},
        ],
        primary_action_label: __("Guardar vínculos"),
        primary_action: async () => {
            if (busy) return;
            const rowName = labels.get(dialog.get_value("detail"));
            if (!rowName) return;
            const detail = rows.find(row => row.name === rowName);
            const error = splitRemittanceTargetsForDetail(frm, detail, dialog.get_value("destinations") || []);
            if (error) {
                frappe.msgprint(error);
                return;
            }
            frm.dirty();
            frm.refresh_field("targets");
            busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                await frm.save();
                dialog.hide();
                frappe.show_alert({message: __("Vínculos guardados. Use Conciliar para validar el detalle y actualizar el resultado."), indicator: "green"});
            } finally {
                busy = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
    loadTargets();
}

function splitRemittanceTargetsForDetail(frm, detail, selections) {
    const targets = frm.doc.targets || [];
    const chosen = new Map(selections.filter(row => row.linked).map(row => [row.target_id, row]));
    const plans = [];
    for (const [name, selection] of chosen) {
        const target = targets.find(row => row.name === name);
        if (!target || (target.detail_row && target.detail_row !== detail.name)) return __("Un destino cambió de fila. Abra nuevamente el selector.");
        const amount = toScaledInteger(selection.amount_usd, 2);
        const original = toScaledInteger(target.amount_usd, 2);
        if (!amount || amount * original <= 0n || (amount < 0n ? -amount : amount) > (original < 0n ? -original : original)) {
            return __("El importe a vincular debe conservar el signo y no superar el importe del destino.");
        }
        if (amount !== original && !target.complementary_item) return __("Solo las partidas complementarias pueden dividirse desde este selector.");
        plans.push({target, amount, remainder: original - amount});
    }
    // Validate every edit before changing the form. The shared claim is never duplicated.
    for (const target of [...targets]) {
        if (target.detail_row === detail.name && !chosen.has(target.name)) {
            target.detail_row = "";
            target.detail_row_label = "";
        }
    }
    for (const {target, amount, remainder} of plans) {
        if (remainder) {
            const remaining = Object.fromEntries(["period", "row_key", "historical_application", "complementary_item", "employer", "notes"].map(key => [key, target[key] || ""]));
            frm.add_child("targets", {...remaining, amount_usd: Number(remainder) / 100, detail_row: "", detail_row_label: ""});
        }
        target.amount_usd = Number(amount) / 100;
        target.detail_row = detail.name;
        target.detail_row_label = `Fila ${detail.source_row || detail.idx} · ${detail.client_name}`;
        if (target.complementary_item && detail.employer) target.employer = detail.employer;
    }
    return "";
}

frappe.ui.form.on("CN Remittance Target", {
    targets_add: renderRemittanceOverview,
    targets_remove: renderRemittanceOverview,
    amount_usd: renderRemittanceOverview,
    employer: renderRemittanceOverview,
    period: renderRemittanceOverview,
    row_key: renderRemittanceOverview,
    historical_application: renderRemittanceOverview,
    complementary_item: renderRemittanceOverview,
    detail_row: renderRemittanceOverview,
});

function downloadRemittanceTemplate(frm) {
    const params = new URLSearchParams({ template_type: "deposito" });
    if (!frm.is_new()) params.set("remittance_name", frm.doc.name);
    const periods = (frm.doc.detail_periods || []).map(row => row.period).filter(Boolean);
    if (periods.length) params.set("period_names", JSON.stringify(periods));
    window.open(
        `/api/method/credinomina_reconciliation.template_download.download_import_template?${params}`,
        "_blank"
    );
}

async function selectRemittanceDetailCredit(frm) {
    if (frm.is_dirty()) await frm.save();
    const rows = frm.doc.detail_rows || [];
    if (!rows.length) return;
    const labels = new Map(rows.map(row => [
        `${row.idx}. ${row.client_name} · Cliente ${row.client_number || row.client || "sin identificar"} · Crédito ${row.loan_number || "sin asignar"}`,
        row.name,
    ]));
    let dialog, credits = new Map(), current = null, requestId = 0, busy = false;
    const escape = value => frappe.utils.escape_html(String(value || ""));
    async function loadCredits() {
        if (!dialog) return;
        const id = ++requestId;
        credits = new Map();
        current = null;
        dialog.get_primary_btn().prop("disabled", true);
        dialog.set_df_property("credit", "options", [""]);
        await dialog.set_value("credit", "");
        const rowName = labels.get(dialog.get_value("detail_row"));
        if (!rowName) return;
        dialog.fields_dict.help.$wrapper.html('<p class="text-muted" role="status">Consultando créditos del cliente…</p>');
        try {
            const response = await frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.get_detail_credits",
                args: { remittance_name: frm.doc.name, detail_row_name: rowName },
            });
            if (id !== requestId) return;
            const data = response.message;
            current = { ...data, rowName };
            credits = new Map(data.credits.map(credit => [
                `${credit.credit_number} · ${credit.credit_status} · Corte ${frappe.datetime.str_to_user(credit.report_date || "")} · ${credit.snapshot}`,
                credit.row_name,
            ]));
            dialog.set_df_property("credit", "options", ["", ...credits.keys()]);
            dialog.fields_dict.help.$wrapper.html(`<p><strong>${escape(data.client_name)}</strong> · Nro. Cliente: ${escape(data.client_number)}</p>
                <p class="text-muted">${credits.size
                    ? "Seleccione el crédito y el corte que respaldan este pago. Se incluyen créditos cancelados para el histórico. No se aplica dinero automáticamente."
                    : "No hay créditos identificados para este cliente y empresa en los cortes de cartera que puede consultar. Revise la carga de cartera y su identificación."}</p>
                <p class="small text-muted">Volver a importar el archivo reemplaza las filas y sus selecciones manuales. El archivo original no se modifica.</p>`);
        } catch (error) {
            if (id === requestId) dialog.fields_dict.help.$wrapper.text("No se pudieron consultar los créditos. Revise la identificación del cliente y sus permisos.");
        }
    }
    dialog = new frappe.ui.Dialog({
        title: __("Vincular crédito desde cartera"), size: "large",
        fields: [
            { fieldname: "detail_row", fieldtype: "Select", label: __("Fila del detalle"), options: [...labels.keys()], onchange: loadCredits },
            { fieldname: "help", fieldtype: "HTML" },
            { fieldname: "credit", fieldtype: "Select", label: __("Nro. Crédito / corte de cartera"), options: [""],
                onchange: () => { if (dialog) dialog.get_primary_btn().prop("disabled", busy || !credits.has(dialog.get_value("credit"))); } },
        ],
        primary_action_label: __("Guardar vínculo"),
        primary_action: async () => {
            const credit = credits.get(dialog.get_value("credit"));
            if (!credit || !current || busy) return;
            busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.set_detail_credit",
                    args: { remittance_name: frm.doc.name, detail_row_name: current.rowName,
                        portfolio_row_name: credit, modified: current.modified },
                    freeze: true, freeze_message: __("Guardando vínculo del crédito…"),
                });
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({ message: __("Crédito vinculado. Use Conciliar para actualizar el resultado."), indicator: "green" });
            } finally {
                busy = false;
                dialog.get_primary_btn().prop("disabled", !credits.has(dialog.get_value("credit")));
            }
        },
    });
    dialog.show();
    await loadCredits();
}

function toggleRemittanceDetailActions(frm) {
    const file = frm.doc.detail_file || frm.doc.support_file || "";
    frm.toggle_display("load_deposit_detail", !frm.is_new() && frm.doc.docstatus !== 2 &&
        !!frm.get_perm(0, "write") && /\.(xlsx|xls|csv)(\?|$)/i.test(file));
}

async function importRemittanceDetail(frm) {
    if (frm.is_dirty()) await frm.save();
    await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.import_remittance_detail",
        args: { remittance_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Cargando detalle del depósito…"),
    });
    await frm.reload_doc();
}

function remittanceCashAmounts(doc) {
    const cents = value => Math.round(Number(value || 0) * 100);
    const total = cents(doc.amount_usd), assigned = cents(doc.allocated_usd), credit = cents(doc.justified_surplus_usd);
    return {assigned: assigned / 100, credit: credit / 100, pending: (total - assigned - credit) / 100};
}

function remittancePresentation(doc, dirty = false) {
    const registration = doc.docstatus === 2 ? "Cancelado" : doc.docstatus === 1 ? "Confirmado" : "Borrador";
    const cash = remittanceCashAmounts(doc);
    const financialReview = !Number.isFinite(cash.pending) || cash.pending !== 0 || cash.credit < 0;
    const recordedSettlement = ["Conciliado", "Conciliado con saldo a favor del cliente"].includes(doc.result)
        || cash.credit > 0 && ["Parcial con saldo a favor", "Saldo a favor documentado"].includes(doc.result);
    const detailReview = (doc.detail_status || "").startsWith("Revisar");
    const reconciled = doc.docstatus === 1 && recordedSettlement && !financialReview && !detailReview;
    const review = doc.docstatus === 1 && (detailReview ||
        ["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(doc.result) || recordedSettlement && financialReview);
    let hint = "Depósito en borrador: todavía no participa en la conciliación. Complete los datos y guarde; después confirme o solicite confirmación a un supervisor.";
    if (doc.docstatus === 2) hint = "Este depósito está cancelado y no participa en la conciliación.";
    else if (doc.docstatus === 1) {
        if (dirty) hint = "Hay cambios sin guardar. Los resultados mostrados corresponden a la última conciliación; guarde y luego use Conciliar.";
        else if (reconciled) hint = cash.credit > 0
            ? "Distribución conciliada con saldo a favor documentado. Revise su gestión en Distribución completa: clasificar el dinero no significa haberlo devuelto o aplicado en el core."
            : "Depósito conciliado. Consulte la distribución y las excepciones en Resultado; conciliar no confirma el registro de ajustes en el core.";
        else if (!doc.detail_count && !(doc.targets || []).length && !review) {
            hint = "Depósito confirmado, pendiente de detalle por cliente o destinos manuales. Puede cargar el archivo cuando llegue, aunque sea en otro mes.";
        }
        else if (doc.result === "Pendiente" || doc.detail_status === "Cargado; pendiente de conciliación") {
            hint = "Hay información pendiente de conciliar. Use Conciliar para actualizar el resultado.";
        } else if (review && Number(doc.allocated_usd) > 0 && Number(doc.unallocated_usd) === 0) {
            hint = "El dinero está distribuido, pero la revisión no ha terminado. Revise los estados y motivos en Detalle por cliente y Destinos.";
        } else if (review) hint = "Revise los estados y motivos en Detalle por cliente y Destinos. Después guarde y use Conciliar.";
        else hint = "Revise el detalle o seleccione partidas en Destinos. Guarde y use Conciliar para actualizar los saldos.";
    }
    return {
        registration, hint,
        result: doc.docstatus === 2 ? "No participa" : doc.docstatus !== 1 ? "Sin conciliar"
            : review && recordedSettlement ? "Por revisar" : reconciled && cash.credit > 0 ? "Conciliado con saldo a favor" : doc.result || "Pendiente",
        color: doc.docstatus === 2 ? "gray" : dirty ? "orange" : reconciled ? "green" : review ? "orange" : "blue",
    };
}

function remittanceMoney(value) {
    const amount = Number(value ?? 0);
    return Number.isFinite(amount) ? amount.toLocaleString("es-NI", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}

function updateDetailPendingAmounts(frm) {
    const manual = new Map();
    for (const target of frm.doc.targets || []) {
        if (!target.detail_row) continue;
        if (!manual.has(target.detail_row)) manual.set(target.detail_row, []);
        manual.get(target.detail_row).push(target);
    }
    // Values have already been rounded to cents by the server. Use integer
    // cents for the immediate preview, without marking the form dirty.
    const cents = value => Math.round((Number(value || 0) + Number.EPSILON) * 100);
    let changed = false;
    for (const row of frm.doc.detail_rows || []) {
        let matched = [];
        try { matched = JSON.parse(row.matched_targets || "[]"); } catch (_) { /* No valid automatic links. */ }
        const entries = manual.get(row.name) || (Array.isArray(matched) ? matched.filter(entry => entry && !entry.instruction_id) : []);
        const linked = entries.reduce((sum, entry) => sum + cents(entry.amount_usd), 0);
        const pending = cents(row.amount_usd) - linked - cents(row.client_credit_usd) - cents(row.company_credit_usd);
        if (row.linked_usd !== linked / 100 || row.pending_usd !== pending / 100) {
            row.linked_usd = linked / 100;
            row.pending_usd = pending / 100;
            changed = true;
        }
    }
    if (changed && frm.refresh_field) frm.refresh_field("detail_rows");
}

function renderRemittanceOverview(frm) {
    updateDetailPendingAmounts(frm);
    if (!frm.dashboard || !frm.$wrapper) return;
    const escape = value => frappe.utils.escape_html(String(value || ""));
    const state = remittancePresentation(frm.doc, frm.is_dirty());
    const confirmed = frm.doc.docstatus === 1;
    const cash = remittanceCashAmounts(frm.doc);
    const cards = [
        ["Equivalente del depósito", `US$ ${remittanceMoney(frm.doc.amount_usd)}`],
        ["Asignado a créditos y otros conceptos", confirmed ? `US$ ${remittanceMoney(cash.assigned)}` : "—"],
        ["Saldo a favor documentado", confirmed ? `US$ ${remittanceMoney(cash.credit)}` : "—"],
        ["Sin asignar ni justificar", confirmed ? `US$ ${remittanceMoney(cash.pending)}` : "—"],
    ];
    const html = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <strong>${escape(frm.doc.employer || "Nuevo depósito")}</strong>
        <span>${escape(state.registration)} · <span class="indicator-pill ${state.color}">${escape(state.result)}</span></span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:12px">
        ${cards.map(([label, value]) => `<div style="padding:12px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg)">
            <div class="small text-muted">${escape(label)}</div><div style="font-size:16px;font-weight:600;margin-top:4px">${escape(value)}</div>
        </div>`).join("")}
    </div><p class="text-muted" style="margin:12px 0 0">${escape(__("Última conciliación: depósito = asignado + saldo a favor documentado + sin asignar ni justificar."))}</p>
    <p>${escape(__("Estado del detalle"))}: ${escape(frm.doc.detail_status || __("Sin detalle cargado"))}${state.result !== frm.doc.result && confirmed ? ` · ${escape(__("Resultado registrado"))}: ${escape(frm.doc.result || __("Pendiente"))}` : ""}</p>
    <p class="text-muted" style="margin:12px 0 0" role="status">${escape(state.hint)}</p>`;
    const existing = frm.$wrapper.find(".cn-remittance-overview");
    if (existing.length) existing.html(html);
    else frm.dashboard.add_section(`<div class="cn-remittance-overview">${html}</div>`);
    frm.dashboard.show();
}

function remittanceAllocationHtml(raw) {
    const escape = value => frappe.utils.escape_html(String(value ?? ""));
    let rows;
    try {
        rows = typeof raw === "string" ? JSON.parse(raw || "[]") : raw || [];
        if (!Array.isArray(rows) || rows.some(row => !row || typeof row !== "object")) throw new Error("Invalid detail");
    } catch (_error) {
        return '<p class="text-muted">No se pudo mostrar el resumen. Revise Información técnica; los datos originales se conservan.</p>';
    }
    if (!rows.length) return '<p class="text-muted">Todavía no hay una distribución registrada. Los destinos seleccionados se procesan al confirmar el depósito y usar Conciliar.</p>';
    return `<div class="table-responsive" style="max-height:400px;overflow:auto"><table class="table table-bordered">
        <caption>Asignaciones de la última conciliación; no son destinos pendientes de guardar.</caption>
        <thead><tr><th>Tipo</th><th>Período / partida</th><th>Crédito / referencia</th><th class="text-right">Importe US$</th><th>Origen</th></tr></thead>
        <tbody>${rows.map(row => `<tr><td>${escape(row.tipo)}</td><td>${escape(row.periodo || row.partida)}</td>
            <td>${escape(row.credito || row.aplicacion_id || row.movimiento || row.fila_id)}</td>
            <td class="text-right text-nowrap">${escape(remittanceMoney(row.importe_usd))}</td><td>${escape(row.origen)}</td></tr>`).join("")}</tbody>
    </table></div>`;
}

function renderRemittanceAllocations(frm) {
    const field = frm.fields_dict.allocation_preview;
    if (field) field.$wrapper.html(remittanceAllocationHtml(frm.doc.allocation_detail));
}

function remittanceDistributionHtml(data, visibleRows = data.rows || [], page = 0) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const size = 100, pages = Math.max(1, Math.ceil(visibleRows.length / size));
    const safePage = Math.min(Math.max(page, 0), pages - 1);
    const allowed = new Set(["CN Reconciliation Period", "CN Complementary Item"]);
    const recordLink = row => allowed.has(row.record_doctype) && row.record_name
        ? `<a href="/app/${row.record_doctype.toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(row.record_name)}">${esc(row.record_name)}</a>` : "—";
    return `<div style="font-size:var(--text-base,14px)">
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px">
            ${[["Depósito US$", data.total_usd], ["Distribuido y documentado US$", data.distributed_usd], ["Pendiente de distribuir US$", data.pending_usd]].map(([label, value]) => `
                <div style="padding:12px;border:1px solid var(--border-color);border-radius:8px"><div>${esc(__(label))}</div>
                <strong style="font-size:18px">${esc(remittanceMoney(value))}</strong></div>`).join("")}
        </div>
        <p>${esc(__("Incluye saldos a favor documentados, aunque su devolución o aplicación futura siga pendiente. No aumenta lo aplicado al crédito."))}</p>
        ${!data.consistent ? `<p class="text-warning" role="alert">${esc(__("El detalle no coincide con la última distribución registrada. Revise los registros y use Conciliar; esta vista no modifica importes."))}</p>` : ""}
        <div class="table-responsive" style="max-height:480px;overflow:auto"><table class="table table-bordered" style="font-size:inherit">
            <thead><tr><th>${__("Concepto")}</th><th>${__("Cliente / empresa")}</th><th>${__("Nro. Crédito")}</th><th>${__("Período / partida")}</th><th class="text-right">${__("Importe US$")}</th><th>${__("Estado / gestión")}</th></tr></thead>
            <tbody>${visibleRows.slice(safePage * size, (safePage + 1) * size).map(row => `<tr>
                <td title="${esc(row.description)}">${esc(row.category)}${row.difference_usd != null ? `<div>${esc(__("Diferencia registrada"))}: US$ ${esc(remittanceMoney(row.difference_usd))}</div>` : ""}</td>
                <td>${row.client_name ? `<strong>${esc(row.client_name)}</strong>` : esc(row.employer || "—")}
                    ${row.client_number ? `<div>${esc(__("Nro. Cliente"))}: ${esc(row.client_number)}</div>` : ""}
                    ${row.client_name && row.employer ? `<div>${esc(row.employer)}</div>` : ""}</td>
                <td>${esc(row.loan_number || "—")}</td><td>${recordLink(row)}${row.period && row.record_doctype === "CN Complementary Item" ? `<div>${esc(row.period)}</div>` : ""}</td>
                <td class="text-right text-nowrap"><strong>${esc(remittanceMoney(row.amount_usd))}</strong></td>
                <td>${esc(row.state || "Distribuido")}${row.management_status ? `<div>${esc(__("Gestión"))}: ${esc(row.management_status)}</div>` : ""}${row.management_pending_usd != null ? `<div>${esc(__("Pendiente de gestión"))}: US$ ${esc(remittanceMoney(row.management_pending_usd))}</div>` : ""}</td>
            </tr>`).join("") || `<tr><td colspan="6">${esc(__("No hay distribuciones registradas que coincidan con el filtro."))}</td></tr>`}</tbody>
            <tfoot><tr><th colspan="4">${esc(__("Total detallado del depósito (todos los registros)"))}</th><th class="text-right text-nowrap">${esc(remittanceMoney(data.detailed_usd))}</th><th></th></tr></tfoot>
        </table></div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
            <span>${esc(__("{0} de {1} registros", [visibleRows.length, (data.rows || []).length]))} · ${esc(__("Página {0} de {1}", [safePage + 1, pages]))}</span>
            <div><button type="button" class="btn btn-default btn-sm" data-distribution-page="-1" ${safePage === 0 ? "disabled" : ""}>${__("Anterior")}</button>
            <button type="button" class="btn btn-default btn-sm" data-distribution-page="1" ${safePage >= pages - 1 ? "disabled" : ""}>${__("Siguiente")}</button></div>
        </div>
    </div>`;
}

async function renderRemittanceDistribution(frm) {
    const field = frm.fields_dict.complete_distribution;
    if (!field) return;
    const wrapper = field.$wrapper, document = frm.doc;
    const token = (frm.distribution_request || 0) + 1;
    frm.distribution_request = token;
    // Remove old closures when another deposit is shown or the view is refreshed.
    wrapper.off(".cnDistribution");
    if (frm.is_new() || document.docstatus !== 1) {
        const message = document.docstatus === 2 ? "El depósito está cancelado; no tiene distribución activa." : "Confirme el depósito y use Conciliar para registrar su distribución. Los destinos seleccionados todavía no son pagos realizados.";
        wrapper.html(`<p>${__(message)}</p>`);
        return;
    }
    wrapper.html(`<p role="status">${__("Consultando la distribución de este depósito…")}</p>`);
    const current = () => frm.doc === document && frm.doc.name === document.name && frm.distribution_request === token;
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.deposit_distribution.get_distribution",
            args: {remittance_name: document.name},
        });
        if (!current()) return;
        const data = response.message;
        if (!data || !Array.isArray(data.rows)) throw new Error("Invalid distribution response");
        if (data.docstatus !== 1) {
            wrapper.html(`<p>${__("El depósito no está confirmado o fue cancelado. Recargue el formulario; no tiene distribución activa.")}</p>`);
            return;
        }
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        wrapper.html(`<p class="text-warning" data-distribution-dirty ${frm.is_dirty() ? "" : "hidden"}>${__("Hay cambios sin guardar. Se muestra la distribución guardada; guarde y concilie para actualizarla.")}</p>
            <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
                <label style="flex:1;min-width:220px">${__("Buscar cliente, empresa, crédito o registro")}<input type="search" class="form-control" data-distribution-search></label>
                <label style="min-width:220px">${__("Concepto")}<select class="form-control" data-distribution-kind><option value="">${__("Todos")}</option>
                    ${[...new Set(data.rows.map(row => row.category))].map(kind => `<option value="${esc(kind)}">${esc(kind)}</option>`).join("")}</select></label>
            </div><div data-distribution-table></div>`);
        let page = 0;
        const fold = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
        function render() {
            if (!current()) return;
            const text = fold(wrapper.find("[data-distribution-search]").val());
            const kind = wrapper.find("[data-distribution-kind]").val();
            const rows = data.rows.filter(row => (!kind || row.category === kind) &&
                fold([row.category, row.client_name, row.client_number, row.loan_number, row.employer, row.record_name, row.period, row.state, row.management_status, row.description].join(" ")).includes(text));
            page = Math.min(Math.max(page, 0), Math.max(0, Math.ceil(rows.length / 100) - 1));
            wrapper.find("[data-distribution-table]").html(remittanceDistributionHtml(data, rows, page));
            wrapper.find("[data-distribution-dirty]").prop("hidden", !frm.is_dirty());
        }
        wrapper.on("input.cnDistribution change.cnDistribution", "[data-distribution-search], [data-distribution-kind]", () => { page = 0; render(); });
        wrapper.on("click.cnDistribution", "[data-distribution-page]", event => { page += Number(event.currentTarget.dataset.distributionPage); render(); });
        render();
    } catch (_error) {
        if (!current()) return;
        wrapper.html(`<p class="text-warning" role="alert">${__("No se pudo consultar la distribución. Use Actualizar distribución para reintentar; no se han cambiado los registros.")}</p>`);
    }
}

async function loadPendingRemittanceTargets(frm, detailRowName = "", selectedIds = null) {
    const response = await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.get_pending_targets",
        args: { remittance_name: frm.doc.name, targets: JSON.stringify(frm.doc.targets || []),
            detail_row_name: detailRowName,
            ...(selectedIds === null ? {} : {selected_ids: JSON.stringify(selectedIds)}) },
        freeze: true,
        freeze_message: __("Consultando partidas pendientes…"),
    });
    return response.message;
}

class RemittanceTargetPicker {
    constructor(frm, data, detailRow = null) {
        this.frm = frm;
        this.data = data;
        this.detailRow = detailRow;
        this.detailRowName = detailRow?.name || data.detail_row || "";
        this.selected = new Map();
        this.page = 0;
        this.pageSize = 50;
        this.available_cents = this.availableFor(data);
        this.detailPeriods = new Set((frm.doc.detail_periods || []).map(row => row.period).filter(Boolean));
        const filterSelectedPeriods = this.detailPeriods.size && !Number(frm.doc.allow_manual_other_periods) ? 1 : 0;
        this.dialog = new frappe.ui.Dialog({
            title: this.detailRowName ? __("Seleccionar partidas pendientes del cliente") : __("Seleccionar partidas pendientes"), size: "extra-large",
            onhide: () => {
                if (this.frm.detail_target_picker === this) this.frm.detail_target_picker = null;
            },
            minimizable: true,
            on_minimize_toggle(minimized) {
                const label = minimized ? __("Restaurar selector de partidas") : __("Minimizar selector de partidas");
                this.get_minimize_btn().attr({ title: label, "aria-label": label });
            },
            fields: [
                { fieldname: "intro", fieldtype: "HTML" },
                { fieldname: "use_detail_periods", fieldtype: "Check", label: __("Usar períodos del detalle"),
                    default: filterSelectedPeriods, read_only: this.detailPeriods.size ? 0 : 1,
                    description: this.detailPeriods.size
                        ? __("Muestra solo partidas de la tabla Períodos del detalle. Desmarque para buscar en otros períodos; las partidas sin período no se muestran mientras esté marcado.")
                        : __("Agregue períodos en la tabla Períodos del detalle del depósito para habilitar este filtro."),
                    onchange: () => this.filterDetailPeriods() },
                { fieldtype: "Section Break" },
                { fieldname: "search", fieldtype: "Data", label: __("Buscar cliente, crédito o referencia"),
                    onchange: () => this.filter() },
                { fieldtype: "Column Break" },
                { fieldname: "period", fieldtype: "Link", options: "CN Reconciliation Period",
                    label: __("Período (opcional)"), onchange: () => this.filter(),
                    get_query: () => {
                        const filters = { employer: ["in", data.allowed_employers || [data.employer]], status: ["!=", "Cerrado"] };
                        if (Number(this.dialog?.get_value("use_detail_periods"))) {
                            filters.name = ["in", [...this.detailPeriods]];
                        }
                        return { filters };
                    } },
                { fieldtype: "Column Break" },
                { fieldname: "kind", fieldtype: "Select", label: __("Tipo de partida"),
                    options: ["Todos", "Cobranza", "Aplicación histórica", "Aplicación del core", "Partida complementaria"],
                    onchange: () => this.filter() },
                { fieldtype: "Section Break" },
                { fieldname: "beneficiary", fieldtype: "Select", label: __("Empresa beneficiaria"),
                    options: ["", ...(data.allowed_employers || [data.employer])], onchange: () => this.filter() },
                { fieldname: "summary", fieldtype: "HTML" },
                { fieldname: "items", fieldtype: "HTML" },
            ],
            primary_action_label: __("Agregar destinos"),
            primary_action: () => this.apply(),
        });
        this.dialog.get_minimize_btn().attr({
            title: __("Minimizar selector de partidas"),
            "aria-label": __("Minimizar selector de partidas"),
        });
        const client = data.detail_client;
        const detailIntro = this.detailRowName ? `<p class="alert alert-info"><strong>${this.escape(client?.client_name || detailRow?.client_name || __("Cliente de la fila"))}</strong>
            ${client?.client_number ? `· ${__("Nro. Cliente")}: ${this.escape(client.client_number)}` : ""}
            ${detailRow?.loan_number ? `· ${__("Crédito")}: ${this.escape(detailRow.loan_number)}` : ""}<br>
            ${__("La lista muestra únicamente partidas pendientes de este cliente. Cada destino agregado quedará vinculado automáticamente a esta fila del detalle.")}</p>` : "";
        this.dialog.fields_dict.intro.$wrapper.html(`<p>Empresa pagadora: <strong>${this.escape(data.employer)}</strong> · US$</p>
            ${detailIntro}
            <p class="text-muted">Seleccione partidas y ajuste los importes si el pago es parcial.
            Puede combinar períodos; no se limita por la fecha del depósito.
            Los saldos corresponden a la última conciliación; se excluyen períodos cerrados y destinos ya agregados.</p>`);
        this.dialog.fields_dict.search.$input.on("input", () => this.filter());
        const wrapper = this.dialog.fields_dict.items.$wrapper;
        wrapper.on("change", "[data-select]", event => {
            const row = this.data.rows[Number(event.target.dataset.select)];
            if (event.target.checked) this.select(row);
            else this.selected.delete(row.id);
            this.render();
        });
        wrapper.on("input", "[data-amount]", event => {
            const row = this.data.rows[Number(event.target.dataset.amount)];
            const raw = event.target.value;
            const valid = /^\d+(?:\.\d{0,2})?$/.test(raw);
            this.selected.set(row.id, valid ? Number(toScaledInteger(raw, 2)) : NaN);
            this.summary();
        });
        wrapper.on("change", "[data-amount]", () => this.render());
        wrapper.on("click", "[data-action]", event => {
            const action = event.currentTarget.dataset.action;
            if (action === "select") this.filtered.forEach(row => this.select(row));
            if (action === "clear") this.selected.clear();
            if (action === "previous") this.page--;
            if (action === "next") this.page++;
            this.render();
        });
        // Frappe applies dialog defaults asynchronously. Seed this control before
        // the first render so the checked filter and the visible rows agree,
        // even while Bootstrap is still showing the modal.
        this.dialog.fields_dict.use_detail_periods.set_input(filterSelectedPeriods);
        this.dialog.show();
        this.render();
    }

    availableFor(data) {
        let available = Number(data.available_cents || 0);
        if (!this.detailRowName) return available;
        // The live row balance already subtracts linked targets, including
        // additions not reconciled yet. Recompute it; do not subtract twice.
        updateDetailPendingAmounts(this.frm);
        const detailRow = (this.frm.doc.detail_rows || []).find(row => row.name === this.detailRowName) || this.detailRow;
        if (!detailRow) return 0;
        const pending = Number.isFinite(Number(detailRow.pending_usd))
            ? Number(toScaledInteger(detailRow.pending_usd, 2))
            : Number(toScaledInteger(detailRow.amount_usd, 2));
        return Math.max(0, Math.min(available, pending));
    }

    escape(value) { return frappe.utils.escape_html(String(value || "")); }
    currency(cents) {
        return (cents / 100).toLocaleString("es-NI", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    total() { return [...this.selected.values()].reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0); }
    valid() {
        return this.selected.size > 0 && this.total() <= this.available_cents &&
            this.data.rows.every(row => !this.selected.has(row.id) || (
                Number.isSafeInteger(this.selected.get(row.id)) && this.selected.get(row.id) > 0 &&
                this.selected.get(row.id) <= row.pending_cents
            ));
    }
    select(row) {
        if (this.selected.has(row.id)) return;
        const amount = Math.min(row.pending_cents, Math.max(0, this.available_cents - this.total()));
        if (amount > 0) this.selected.set(row.id, amount);
    }
    filter() { this.page = 0; if (this.dialog && this.dialog.$wrapper.is(":visible")) this.render(); }
    filterDetailPeriods() {
        if (!this.dialog) return;
        if (Number(this.dialog.get_value("use_detail_periods")) && this.dialog.get_value("period") &&
            !this.detailPeriods.has(this.dialog.get_value("period"))) {
            // An old single-period filter must not conflict with the table's scope.
            this.dialog.set_value("period", "");
        }
        this.filter();
    }
    summary() {
        const remaining = this.available_cents - this.total();
        const invalid = this.selected.size && !this.valid();
        this.dialog.fields_dict.summary.$wrapper.html(`<div class="alert ${invalid ? "alert-danger" : "alert-info"}" role="status" aria-live="polite">
            <div style="display:flex;flex-wrap:wrap;gap:12px 28px">
                <span>${this.detailRowName ? __("Pendiente de esta fila") : __("Disponible")}: <strong>US$ ${this.currency(this.available_cents)}</strong></span>
                <span>Seleccionado (${this.selected.size}): <strong>US$ ${this.currency(this.total())}</strong></span>
                <span>Restante: <strong>US$ ${this.currency(remaining)}</strong></span>
            </div>
            ${invalid ? "<div>Revise los importes: deben ser positivos, tener hasta dos decimales y no superar el pendiente ni el disponible.</div>" : ""}
            ${!this.available_cents ? `<div>${this.detailRowName ? __("La fila no tiene importe pendiente disponible para nuevos destinos.") : __("El depósito ya está distribuido o reservado en sus destinos. Revise los destinos existentes.")}</div>` : ""}
            <small>El disponible considera las asignaciones conciliadas y los destinos existentes, incluidos los ajustes complementarios negativos.</small>
        </div>`);
        this.dialog.get_primary_btn().prop("disabled", !this.valid());
        this.dialog.fields_dict.items.$wrapper.find("[data-select]:not(:checked)")
            .prop("disabled", remaining <= 0);
    }
    render() {
        const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
        const query = normalize(this.dialog.fields_dict.search.$input.val()).trim().split(/\s+/).filter(Boolean);
        const period = this.dialog.get_value("period");
        const kind = this.dialog.get_value("kind");
        const beneficiary = this.dialog.get_value("beneficiary");
        const useDetailPeriods = Number(this.dialog.get_value("use_detail_periods"));
        const filtered = this.data.rows.filter(row => {
            const text = normalize([row.client_name, row.client_number, row.national_id, row.employee_number,
                row.loan_number, row.reference, row.period_label].join(" "));
            return (!useDetailPeriods || this.detailPeriods.has(row.filter_period)) &&
                (!beneficiary || row.employer === beneficiary) && (!period || row.filter_period === period) && (!kind || kind === "Todos" || row.kind === kind)
                && query.every(word => text.includes(word));
        });
        const pages = Math.max(1, Math.ceil(filtered.length / this.pageSize));
        this.filtered = filtered;
        this.page = Math.max(0, Math.min(this.page, pages - 1));
        this.visible = filtered.slice(this.page * this.pageSize, (this.page + 1) * this.pageSize);
        const rows = this.visible.map(row => {
            const index = this.data.rows.indexOf(row);
            const selected = this.selected.has(row.id);
            const amount = this.selected.get(row.id);
            const identity = [["Cliente", row.client_number], ["Cédula", row.national_id], ["Empleado", row.employee_number]]
                .filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`).join(" · ");
            return `<tr class="${selected ? "active" : ""}">
                <td><input type="checkbox" data-select="${index}" aria-label="Seleccionar ${this.escape(row.client_name || row.loan_number)}"
                    ${selected ? "checked" : ""} ${!selected && this.total() >= this.available_cents ? "disabled" : ""}></td>
                <td><strong>${this.escape(row.client_name || "Sin nombre informado")}</strong>
                    <div class="small text-muted">${this.escape(identity)}</div>
                    <div>${row.loan_number ? `Crédito: ${this.escape(row.loan_number)}` : ""}</div></td>
                <td>${this.escape(row.employer)}</td>
                <td>${this.escape(row.kind)}<div class="small text-muted">${this.escape(row.period_label)}</div>
                    <div class="small">${this.escape(row.reference)}</div></td>
                <td class="text-right text-nowrap">${row.applied_cents == null ? '<span title="No corresponde a una aplicación en el core">—</span>' : this.currency(row.applied_cents)}</td>
                <td class="text-right text-nowrap">${this.currency(row.assigned_cents)}</td>
                <td class="text-right text-nowrap">${this.currency(row.pending_cents)}</td>
                <td style="min-width:135px"><input class="form-control input-sm" type="number" min="0.01" step="0.01"
                    max="${row.pending_cents / 100}" data-amount="${index}" aria-label="Asignar US$ a ${this.escape(row.client_name || row.loan_number)}"
                    value="${selected && Number.isFinite(amount) ? (amount / 100).toFixed(2) : ""}" ${selected ? "" : "disabled"}>
                    ${selected && amount < row.pending_cents ? '<small class="text-muted">Pago parcial</small>' : ""}</td>
            </tr>`;
        }).join("");
        this.dialog.fields_dict.items.$wrapper.html(`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
            <button type="button" class="btn btn-default btn-sm" data-action="select">Seleccionar resultados hasta cubrir saldo</button>
            <button type="button" class="btn btn-default btn-sm" data-action="clear">Limpiar selección</button>
            <span class="text-muted">${filtered.length} partidas · Página ${this.page + 1} de ${pages}</span>
        </div>
        <div class="table-responsive" style="max-height:380px;overflow:auto">
            <table class="table table-bordered table-hover"><thead style="position:sticky;top:0;background:var(--card-bg,white);z-index:1"><tr>
                <th style="width:36px"><span class="sr-only">Seleccionar</span></th><th>Cliente / crédito</th><th>Empresa</th><th>Origen / referencia</th>
                <th class="text-right text-nowrap" title="Importe aplicado en el core">Aplicado US$</th>
                <th class="text-right text-nowrap" title="Importe ya distribuido desde depósitos confirmados">Asignado US$</th>
                <th class="text-right text-nowrap" title="Saldo disponible para asignar; considera deducciones, partidas complementarias y ajustes de conciliación">Pendiente US$</th><th class="text-nowrap">Asignar US$</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="8" class="text-center text-muted">No hay partidas pendientes con estos filtros. Verifique las aplicaciones históricas o las deducciones de la empresa y ejecute Conciliar para actualizar los saldos.</td></tr>'}</tbody></table>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
            <button type="button" class="btn btn-default btn-sm" data-action="previous" ${this.page === 0 ? "disabled" : ""}>Anterior</button>
            <button type="button" class="btn btn-default btn-sm" data-action="next" ${this.page + 1 >= pages ? "disabled" : ""}>Siguiente</button>
        </div><p class="small text-muted" style="margin-top:12px">Seleccionar resultados incluye todas las páginas del filtro, en el orden mostrado; la última partida puede quedar parcial. La selección se conserva al cambiar filtros. Agregar solo completa los destinos: después guarde y use Conciliar.</p>`);
        this.summary();
    }
    async apply() {
        if (!this.valid() || this.applying) return;
        this.applying = true;
        try {
            // Re-read balances before adding; never silently truncate the user's selection.
            const fresh = await loadPendingRemittanceTargets(this.frm, this.detailRowName, [...this.selected.keys()]);
            const byId = new Map(fresh.rows.map(row => [row.id, row]));
            const freshAvailable = this.availableFor(fresh);
            if (fresh.modified !== this.data.modified || this.total() > freshAvailable ||
                [...this.selected].some(([id, cents]) => !byId.has(id) || cents > byId.get(id).pending_cents)) {
                frappe.msgprint(__("Los saldos o destinos cambiaron. Cierre el selector y vuelva a consultar las partidas antes de agregarlas."));
                return;
            }
            for (const [id, cents] of this.selected) {
                const row = byId.get(id);
                this.frm.add_child("targets", {
                    period: row.period || "", row_key: row.row_key || "",
                    historical_application: row.historical_application || "",
                    complementary_item: row.complementary_item || "", amount_usd: cents / 100,
                    employer: row.generic_distribution ? row.employer : "",
                    detail_row: this.detailRowName,
                    detail_row_label: this.detailRowName && this.detailRow
                        ? `${__("Fila")} ${this.detailRow.source_row || this.detailRow.idx} · ${this.detailRow.client_name || this.data.detail_client?.client_name || ""}` : "",
                    notes: [row.employer, row.client_name, row.loan_number && `Crédito ${row.loan_number}`, row.period_label, row.reference].filter(Boolean).join(" · "),
                });
            }
            this.frm.refresh_field("targets");
            this.frm.dirty();
            renderRemittanceOverview(this.frm);
            this.dialog.hide();
            frappe.show_alert({ message: this.detailRowName
                ? __("Destinos agregados y vinculados a la fila. Guarde los cambios y después use Conciliar.")
                : __("Destinos agregados. Guarde los cambios; la conciliación se ejecuta por separado."), indicator: "green" });
        } finally {
            this.applying = false;
        }
    }
}

function updateUsdEquivalent(frm) {
    const amountCents = toScaledInteger(frm.doc.deposit_amount, 2);
    const currency = frm.doc.deposit_currency;
    const rateScaled = toScaledInteger(frm.doc.fx_rate, 8);
    let usdCents = 0n;
    if (currency === "USD") {
        usdCents = amountCents;
    } else if (currency === "NIO" && rateScaled > 0n) {
        // amountCents / (rateScaled / 1e8), rounded half-up to USD cents.
        const numerator = amountCents * 100000000n;
        usdCents = (numerator + rateScaled / 2n) / rateScaled;
    }
    frm.set_value("amount_usd", Number(usdCents) / 100);
}

function toScaledInteger(value, decimalPlaces) {
    const raw = String(value || 0).trim();
    const match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?$/);
    if (!match) return 0n;
    const factor = 10n ** BigInt(decimalPlaces);
    const fraction = ((match[3] || "") + "0".repeat(decimalPlaces)).slice(0, decimalPlaces);
    const scaled = BigInt(match[2]) * factor + BigInt(fraction || "0");
    return match[1] === "-" ? -scaled : scaled;
}


function unreconcileRemittance(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Desconciliar depósito"),
        fields: [
            {fieldtype: "HTML", options: `<div class="alert alert-warning">${__("Se retirarán las asignaciones de este depósito y se recalcularán los saldos de los períodos afectados. El depósito seguirá confirmado y quedará pendiente de conciliación. La operación se registrará en el historial.")}</div>`},
            {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo"), reqd: 1},
        ],
        primary_action_label: __("Desconciliar"),
        primary_action(values) {
            if (dialog.running) return;
            dialog.running = true;
            dialog.disable_primary_action();
            frappe.confirm(__("¿Confirma que desea desconciliar este depósito?"), async () => {
                try {
                    if (frm.is_dirty()) await frm.save();
                    await frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.unreconcile_remittance",
                        args: {remittance_name: frm.doc.name, modified: frm.doc.modified, reason: values.reason},
                        freeze: true, freeze_message: __("Desconciliando depósito…"),
                    });
                    dialog.hide();
                    await frm.reload_doc();
                    frappe.show_alert({message: __("Depósito desconciliado."), indicator: "green"});
                } finally { dialog.running = false; dialog.enable_primary_action(); }
            }, () => { dialog.running = false; dialog.enable_primary_action(); });
        },
    });
    dialog.show();
}
