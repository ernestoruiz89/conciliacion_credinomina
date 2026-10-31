function linkAccountingOrigins(frm) {
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde el depósito antes de vincular su origen contable."));
    const depositName = frm.doc.name;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const method = action => `credinomina_reconciliation.manual_origin_link.${action}`;
    let dialog, checked = null, loadedKind = null, sequence = 0;
    const kind = () => dialog.get_value("kind") === "Depósito del core" ? "deposit" : "internal";
    const invalidate = () => {
        checked = null;
        dialog.fields_dict.result.$wrapper.empty();
        dialog.set_primary_action(__("Comprobar selección"), submit);
    };
    const reset = () => {
        if (!dialog) return;
        sequence++;
        loadedKind = null;
        dialog.fields_dict.rows.df.data = [];
        dialog.fields_dict.rows.grid.refresh();
        invalidate();
    };
    async function search() {
        reset();
        const current = sequence, sourceKind = kind();
        dialog.disable_primary_action();
        try {
            const response = await frappe.call({method: method("candidates"), args: {
                deposit_name: depositName, kind: sourceKind,
                reference: dialog.get_value("reference"), voucher: dialog.get_value("voucher"),
            }});
            if (current !== sequence) return;
            loadedKind = sourceKind;
            dialog.fields_dict.rows.df.data = (response.message || []).map(row => ({
                record_name: row.name, date: sourceKind === "internal" ? row.source_date : row.deposit_date,
                reference: sourceKind === "internal" ? (row.reference || row.accounting_reference) : row.deposit_reference,
                accounting_reference: sourceKind === "internal" ? row.accounting_reference : "",
                voucher: sourceKind === "internal" ? row.source_voucher : row.deposit_voucher,
                currency: sourceKind === "internal" ? row.source_currency : row.deposit_currency,
                amount: sourceKind === "internal" ? row.source_credit : row.deposit_amount,
                description: row.source_description,
            }));
            dialog.fields_dict.rows.grid.refresh();
            dialog.fields_dict.result.$wrapper.html(`<p>${__("Resultados")}: ${(response.message || []).length}. ${__("Seleccione las filas y compruebe el total antes de vincular.")}</p>`);
        } finally { if (current === sequence) dialog.enable_primary_action(); }
    }
    async function submit() {
        if (frm.doc.name !== depositName || frm.is_dirty()) return frappe.msgprint(__("El depósito cambió. Cierre el modal, guarde y vuelva a abrirlo."));
        const selection = dialog.fields_dict.rows.grid.get_selected_children().map(row => row.record_name).sort();
        const sourceKind = kind();
        if (!selection.length || loadedKind !== sourceKind) return frappe.msgprint(__("Busque y seleccione los movimientos que respaldan este depósito."));
        if (sourceKind === "deposit" && selection.length !== 1) return frappe.msgprint(__("Seleccione un único depósito del core."));
        const signature = JSON.stringify([sourceKind, selection]);
        dialog.disable_primary_action();
        try {
            if (!checked || checked.signature !== signature) {
                const response = await frappe.call({method: method("preview"), args: {deposit_name: depositName, kind: sourceKind, selection}});
                const result = response.message;
                checked = {signature, ...result};
                const selectedTotal = Object.entries(result.totals_by_currency || {[result.currency]: result.total})
                    .map(([currency, amount]) => `${esc(currency)} ${esc(Number(amount).toFixed(2))}`).join(" · ");
                dialog.fields_dict.result.$wrapper.html(`<div class="alert alert-${result.issues.length ? "warning" : "success"}">
                    <p>${__("Líneas contables")}: ${result.count} · ${__("Total seleccionado")}: ${selectedTotal} · ${__("Depósito")}: ${esc(result.currency)} ${esc(Number(result.deposit_amount).toFixed(2))} · ${__("Diferencia")}: ${result.difference == null ? __("No comparable: monedas distintas") : esc(Number(result.difference).toFixed(2))}</p>
                    ${result.issues.length ? `<ul>${result.issues.map(issue => `<li>${esc(issue)}</li>`).join("")}</ul><p>${__("La selección quedará pendiente de revisión. No se agregarán líneas ni se cambiarán saldos.")}</p>` : `<p>${__("Se conservarán el depósito, sus importes y la conciliación realizada. Las líneas originales se agregarán al origen contable.")}</p>`}</div>`);
                dialog.set_primary_action(__(result.issues.length ? "Dejar para revisión" : "Vincular al depósito"), submit);
                return;
            }
            const reason = dialog.get_value("reason");
            if (!String(reason || "").trim()) return frappe.msgprint(__("Indique cómo verificó que corresponde al mismo depósito."));
            const response = await frappe.call({method: method("link"), args: {
                deposit_name: depositName, kind: sourceKind, selection, token: checked.token, reason,
            }, freeze: true, freeze_message: __("Guardando el origen contable…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.show_alert({message: __(response.message.status === "Vinculado" ? "Origen contable vinculado. Se conservó la conciliación del depósito." : "Diferencias guardadas para revisión. No se modificó la conciliación."),
                indicator: response.message.status === "Vinculado" ? "green" : "orange"});
        } catch (error) {
            invalidate();
            throw error;
        } finally { dialog.enable_primary_action(); }
    }
    dialog = new frappe.ui.Dialog({title: __("Vincular origen contable al depósito"), size: "extra-large", fields: [
        {fieldtype: "HTML", options: `<p>${__("Seleccione el origen del mismo ingreso. La fecha de carga puede ser posterior; se comparan la empresa, referencia, fecha del depósito, moneda e importe. Esta acción conserva la distribución ya realizada.")}</p>`},
        {fieldname: "kind", fieldtype: "Select", label: __("Tipo de origen"), options: "Movimientos internos\nDepósito del core", default: "Movimientos internos", onchange: reset,
            description: __("Las partidas internas se enviarán como Vinculado a depósito, con su referencia y tratamiento Partida de depósito. Se requiere permiso para enviar. El depósito del core debe estar en borrador, sin detalle ni destinos.")},
        {fieldname: "reference", fieldtype: "Data", label: __("Referencia"), default: frm.doc.deposit_reference, onchange: reset,
            description: __("Busca en la referencia del depósito y en la referencia contable original del movimiento.")},
        {fieldname: "voucher", fieldtype: "Data", label: __("Asiento (opcional)"), onchange: reset,
            description: __("Filtro de búsqueda. Déjelo vacío para seleccionar movimientos internos de distintos asientos; cada uno conserva su asiento original.")},
        {fieldname: "search", fieldtype: "Button", label: __("Buscar movimientos"), click: search},
        {fieldname: "select_all", fieldtype: "Button", label: __("Seleccionar todas las partidas"), click() {
            if (kind() !== "internal") return frappe.msgprint(__("Seleccione un único depósito del core."));
            (dialog.fields_dict.rows.df.data || []).forEach(row => { row.__checked = 1; });
            dialog.fields_dict.rows.grid.refresh();
            invalidate();
        }},
        {fieldname: "rows", fieldtype: "Table", label: __("Movimientos disponibles"), cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: false, data: [], fields: [
            {fieldname: "record_name", fieldtype: "Data", label: __("Documento"), read_only: 1, in_list_view: 1, columns: 3},
            {fieldname: "date", fieldtype: "Date", label: __("Fecha"), read_only: 1, in_list_view: 1, columns: 2},
            {fieldname: "reference", fieldtype: "Data", label: __("Referencia"), read_only: 1, in_list_view: 1, columns: 2},
            {fieldname: "accounting_reference", fieldtype: "Data", label: __("Referencia contable original"), read_only: 1},
            {fieldname: "currency", fieldtype: "Data", label: __("Moneda"), read_only: 1, in_list_view: 1, columns: 1},
            {fieldname: "amount", fieldtype: "Currency", label: __("Importe"), options: "currency", read_only: 1, in_list_view: 1, columns: 2},
            {fieldname: "voucher", fieldtype: "Data", label: __("Asiento original"), read_only: 1},
            {fieldname: "description", fieldtype: "Small Text", label: __("Descripción original"), read_only: 1},
        ]},
        {fieldname: "result", fieldtype: "HTML"},
        {fieldname: "reason", fieldtype: "Small Text", label: __("Verificación y motivo del vínculo"), description: __("Identifique el comprobante o evidencia que confirma que es el mismo ingreso.")},
    ], primary_action_label: __("Comprobar selección"), primary_action: submit});
    dialog.show();
    search();
}

function reviewDepositOrigin(frm) {
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde los cambios antes de revisar el origen."));
    const canLink = frm.doc.docstatus === 0 && !(frm.doc.targets || []).length && !(frm.doc.detail_rows || []).length && !frm.doc.detail_file && !!frm.doc.accounting_source_key;
    const dialog = new frappe.ui.Dialog({
        title: __("Revisar origen del depósito"),
        fields: [
            {fieldtype: "HTML", options: `<p>${__("Esta acción verifica si existe un solo efectivo o dos depósitos independientes. Conserva las líneas contables, archivos y documentos; no redistribuye dinero ni elimina registros.")}</p>`},
            {fieldname: "action", fieldtype: "Select", label: __("Resultado de la revisión"),
                options: canLink ? "Vincular origen a otro depósito\nVerificar efectivo independiente" : "Verificar efectivo independiente",
                default: canLink ? "Vincular origen a otro depósito" : "Verificar efectivo independiente", reqd: 1},
            {fieldname: "canonical_name", fieldtype: "Link", options: "CN Remittance Allocation", label: __("Depósito que representa el efectivo"),
                depends_on: "eval:doc.action === 'Vincular origen a otro depósito'", mandatory_depends_on: "eval:doc.action === 'Vincular origen a otro depósito'",
                get_query: () => ({filters: {employer: frm.doc.employer, deposit_reference: frm.doc.deposit_reference, docstatus: ["!=", 2], name: ["!=", frm.doc.name]}})},
            {fieldname: "reason", fieldtype: "Small Text", label: __("Evidencia y motivo de la decisión"), reqd: 1,
                description: __("Identifique cómo verificó el banco, referencia, fecha e importe. No use esta opción para ocultar una diferencia contable.")},
        ],
        primary_action_label: __("Guardar revisión del origen"),
        async primary_action(values) {
            dialog.disable_primary_action();
            try {
                const link = values.action === "Vincular origen a otro depósito";
                await frappe.call({method: `credinomina_reconciliation.deposit_accounting_sources.${link ? "link_duplicate_origin" : "verify_independent_deposit"}`,
                    args: {deposit_name: frm.doc.name, modified: frm.doc.modified, reason: values.reason,
                        ...(link ? {canonical_name: values.canonical_name} : {})}, freeze: true});
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __("Origen revisado. Se conserva la evidencia contable; no se redistribuyó dinero."), indicator: "green"});
            } finally { dialog.enable_primary_action(); }
        },
    });
    dialog.show();
}

function targetDisplayLabel(target, details = []) {
    if (target.notes) return String(target.notes);
    const detail = details.find(row => row.name === target.detail_row);
    const identity = detail ? [detail.client_name, detail.loan_number && `${__("Crédito")} ${detail.loan_number}`].filter(Boolean).join(" · ") : target.detail_row_label;
    const kind = target.complementary_item ? `${__("Partida complementaria")} · ${target.complementary_item}`
        : target.historical_application ? __("Aplicación vinculada")
        : target.period ? `${__("Período")} · ${target.period}` : __("Seleccione una partida pendiente");
    return [identity, kind].filter(Boolean).join(" · ");
}

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
    link_accounting_origins(frm) { linkAccountingOrigins(frm); },
    onload(frm) {
        if (frm.is_new() && Number(frm.doc.apply_fifo) !== 1) {
            frm.set_value("apply_fifo", 1);
        }
    },
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
        window.credinominaWorkbenchHandoff?.(frm, result => openWorkbenchDepositSelection(frm, result));
        if (frm.doc.cash_identity_status === "Origen vinculado" || frm.doc.consolidated_deposit) {
            frm.dashboard.set_headline_alert(__("Este documento conserva un origen contable; no representa otro efectivo. Consulte el depósito vinculado."), "blue");
            frm.add_custom_button(__("Abrir depósito vinculado"), () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.consolidated_deposit));
            frm.disable_save();
            for (const field of ["link_accounting_origins", "load_deposit_detail", "use_applications_detail", "use_collection_detail",
                "select_detail_credit", "refresh_distribution", "select_pending_targets", "link_detail_targets", "create_complementary"]) {
                frm.toggle_display(field, false);
            }
            return;
        }
        if (frm.doc.cash_identity_status === "Revisar identidad") {
            frm.dashboard.set_headline_alert(__("Posible repetición de efectivo. El estado de cuenta muestra saldo no determinado hasta revisar el origen; no confirme ni concilie antes de verificarlo."), "orange");
        }
        if (!frm.is_new() && frm.doc.docstatus !== 2 && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Revisar origen del depósito"), () => reviewDepositOrigin(frm), __("Más opciones"));
            if ((frm.doc.detail_rows || []).length) {
                frm.add_custom_button(__("Actualizar estado del crédito"), () => refreshRemittanceCreditStatus(frm), __("Más opciones"));
                frm.add_custom_button(__("Documentar diferencias del detalle"), () => reviewRemittanceDifferences(frm), __("Más opciones"));
            }
        }
        frm.toggle_display("link_accounting_origins", !frm.is_new() && frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        loadPayingCompanies(frm);
        const targetFormatter = (value, df, options, row) => {
            const label = targetDisplayLabel(row || {});
            return `<span title="${frappe.utils.escape_html(label)}">${frappe.utils.escape_html(label)}</span>`;
        };
        // v15 reads the grid DocField; v16 reads the shared metadata formatter.
        const targetNotes = frappe.meta?.get_docfield?.("CN Remittance Target", "notes");
        if (targetNotes) targetNotes.formatter = targetFormatter;
        frm.fields_dict.targets?.grid?.update_docfield_property?.("notes", "formatter", targetFormatter);
        setupRemittanceClientAlias(frm);
        setupRemittanceLinkedTotal(frm);
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
            frm.add_custom_button(__("Ver Partidas Complementarias"),
                () => showRemittanceComplementaryItems(frm), __("Conciliación"));
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
    use_collection_detail(frm) { return useApplicationsAsDetail(frm, "collection"); },
    detail_file(frm) { toggleRemittanceDetailActions(frm); renderRemittanceOverview(frm); },
    support_file: toggleRemittanceDetailActions,
    amount_usd: renderRemittanceOverview,
    detail_total_usd: renderRemittanceOverview,
    detail_count: renderRemittanceOverview,
    employer(frm) { loadPayingCompanies(frm); renderRemittanceOverview(frm); toggleApplicationDetailAction(frm); },
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
                <p>${esc(__("Se usará la cobranza conciliada contra aplicaciones. El detalle quedará identificado como generado, no como archivo enviado por la empresa. Las partidas reales conservarán sus obligaciones de seguimiento."))}</p>
                <p><b>${esc(__("Base / depósito"))}: ${amount(plan.total_usd)}</b> · ${esc(__("Aplicado"))}: ${amount(plan.applied_usd)} · ${esc(__("Ajustes"))}: ${amount(plan.adjustment_usd)}</p>
                <div class="table-responsive" style="max-height:45vh;overflow:auto"><table class="table table-bordered">
                <thead><tr>${["Período", "Cliente", "Crédito", "Base", "Base US$", "Aplicado US$", "Ajuste US$", "Clasificación"].map(x => `<th>${esc(__(x))}</th>`).join("")}</tr></thead>
                <tbody>${plan.rows.map(row => {
                    const adjustment = plan.adjustments.find(x => x.collection_row === row.collection_row);
                    return `<tr><td>${esc(row.period)}</td><td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(row.basis)}</td>
                    <td class="text-right">${amount(row.base_usd)}</td><td class="text-right">${amount(row.applied_usd)}</td><td class="text-right">${amount(row.amount_usd)}</td><td>${esc(adjustment?.category || "Sin diferencia")}</td></tr>`;
                }).join("")}</tbody></table></div>`},
            {fieldname: "confirm_correspondence", fieldtype: "Check", reqd: 1,
                label: __("Confirmo que este depósito corresponde a las cobranzas mostradas, no solo que el monto coincide")},
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
            {fieldname: "deposit_amount", fieldtype: "Currency", options: frm.doc.deposit_currency,
                label: __("Importe del depósito ({0})", [frm.doc.deposit_currency]), precision: 2,
                reqd: 1, default: frm.doc.deposit_amount,
                description: __("Use el total recibido según el soporte bancario. El crédito contable original puede ser solo una línea del asiento y no se modifica aquí.")},
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
                    fields: [{fieldname: "reason", fieldtype: "Small Text", label: __("Motivo (opcional)"),
                        description: __("Puede indicar qué está verificando o corrigiendo. Si cambia la distribución, se conservarán el antes y el después.")}],
                    primary_action_label: __("Conciliar"),
                    primary_action(values) { accepted = true; resolve(values.reason || ""); dialog.hide(); },
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
        frappe.msgprint({
            title: __("Conciliación finalizada"),
            indicator: remittancePresentation(frm.doc).color,
            wide: true,
            message: remittanceReconciliationSummaryHtml(frm.doc, result),
        });
    } finally {
        frappe.realtime.off(event, onProgress);
        frm.reconciliation_running = false;
    }
}

function remittanceReconciliationSummaryHtml(doc, result = {}) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const state = remittancePresentation(doc);
    const cash = remittanceCashAmounts(doc);
    const accent = {green: "var(--green-500, #22a06b)", orange: "var(--orange-500, #d97706)",
        blue: "var(--blue-500, #2490ef)", gray: "var(--gray-500, #6b7280)"}[state.color];
    const amount = value => value == null || !Number.isFinite(Number(value)) ? "—" : `US$ ${remittanceMoney(value)}`;
    const amounts = [["Depositado", doc.amount_usd], ["Distribuido", cash.assigned],
        ["Saldo a favor documentado", cash.credit], ["Sin distribuir", cash.pending]];
    const counts = [["Filas del detalle", result.detail_rows], ["Conciliadas", result.detail_matched],
        ["Pendientes", result.detail_pending], ["Destinos", result.targets]];
    return `<section aria-label="${esc(__("Resumen de conciliación"))}" style="color:var(--text-color);padding:4px 0">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;padding:18px 20px;border:1px solid var(--border-color);border-left:4px solid ${accent};border-radius:10px;background:var(--control-bg);margin-bottom:20px">
            <div style="min-width:0;flex:1 1 220px">
                <div class="text-muted" style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin-bottom:6px">${esc(__("Resumen de conciliación"))}</div>
                <div style="font-size:17px;font-weight:600;overflow-wrap:anywhere">${esc(result.deposit || doc.name)}</div>
                ${doc.employer ? `<div class="text-muted" style="font-size:12px;margin-top:4px;overflow-wrap:anywhere">${esc(doc.employer)}</div>` : ""}
            </div>
            <span class="indicator-pill ${state.color}" style="height:auto;white-space:normal;max-width:100%;text-align:left">${esc(__(state.result))}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;margin-bottom:20px">
            ${amounts.map(([label, value]) => `<div style="padding:14px;border:1px solid var(--border-color);border-radius:8px;background:var(--fg-color)">
                <div class="text-muted" style="font-size:12px;min-height:34px;line-height:1.4">${esc(__(label))}</div>
                <div style="font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;overflow-wrap:anywhere">${esc(amount(value))}</div>
            </div>`).join("")}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;padding:16px;background:var(--control-bg);border-radius:8px;margin-bottom:18px">
            ${counts.map(([label, value]) => `<div style="text-align:center">
                <div style="font-size:24px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums">${esc(value ?? 0)}</div>
                <div class="text-muted" style="font-size:12px;margin-top:5px">${esc(__(label))}</div>
            </div>`).join("")}
        </div>
        <div style="border-top:1px solid var(--border-color);padding-top:14px">
            <div class="text-muted" style="font-size:12px;margin-bottom:5px">${esc(__("Períodos afectados"))}</div>
            <div style="font-size:13px;overflow-wrap:anywhere">${esc((result.periods || []).join(", ") || "—")}</div>
        </div>
    </section>`;
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
    const visible = (frm.doc.detail_periods || []).some(row => row.period) &&
        frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write");
    frm.toggle_display("use_applications_detail", visible);
    if (visible) frm.fields_dict.use_applications_detail?.$wrapper.css({display: "inline-block", marginRight: "8px"});
    return toggleCollectionDetailAction(frm, visible);
}

async function toggleCollectionDetailAction(frm, enabled) {
    const request = frm._collection_detail_request = (frm._collection_detail_request || 0) + 1;
    frm.toggle_display("use_collection_detail", false);
    if (!enabled || !frm.doc.employer) return;
    const document = frm.doc;
    const employer = document.employer;
    const periods = () => JSON.stringify((frm.doc.detail_periods || []).map(row => row.period).filter(Boolean));
    const selection = periods();
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.collection_detail_available",
            args: {employer, periods: selection},
        });
        if (request !== frm._collection_detail_request || document !== frm.doc || employer !== frm.doc.employer || selection !== periods()) return;
        const visible = response.message === true && frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write");
        frm.toggle_display("use_collection_detail", visible);
        if (visible) frm.fields_dict.use_collection_detail?.$wrapper.css({display: "inline-block"});
    } catch (error) {
        // Frappe displays request errors; keep the action hidden on lookup failure.
        if (request === frm._collection_detail_request) frm.toggle_display("use_collection_detail", false);
    }
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

async function useApplicationsAsDetail(frm, source = "applications") {
    const fromCollection = source === "collection";
    if (!(frm.doc.detail_periods || []).some(row => row.period)) {
        frappe.msgprint(__("Seleccione al menos un período del detalle."));
        return;
    }
    if (frm.is_new() || frm.is_dirty()) await frm.save();
    const method = "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.";
    const response = await frappe.call({method: method + (fromCollection ? "preview_collection_detail" : "preview_application_detail"),
        args: {remittance_name: frm.doc.name}, freeze: true,
        freeze_message: __(fromCollection ? "Consultando cobranza de los períodos…" : "Consultando aplicaciones pendientes de los períodos…")});
    const preview = response.message;
    if (!preview.rows.length) {
        frappe.msgprint(__(fromCollection ? "No hay filas de cobranza con importe positivo en los períodos seleccionados." : "No hay aplicaciones pendientes para los períodos seleccionados."));
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
        title: __(fromCollection ? "Usar cobranza como detalle" : "Usar aplicaciones pendientes como detalle"), size: "extra-large",
        fields: [{fieldname: "selection_preview", fieldtype: "HTML", options: `
            <p>${__(fromCollection ? "Se generará un archivo privado con la cobranza cargada de los períodos {0}. Los importes originales de cobranza se proponen completos, sin descontar otros depósitos; seleccione las filas correspondientes y ajuste los importes para este depósito." : "Se generará un archivo privado con las aplicaciones pendientes de los períodos {0}, descontando lo cubierto por otros depósitos. Las asignaciones de este depósito se conservan para poder completar su propio detalle.", [esc(preview.periods.join(", "))])}</p>
            <p><strong>${__(fromCollection ? "Cobranza de los períodos" : "Aplicado a los períodos")}: US$ ${remittanceMoney(fromCollection ? preview.collection_usd : preview.applied_usd)} ·
                ${__("Seleccionado")}: US$ <span data-selected-total>${remittanceMoney(preview.total_usd)}</span> ·
                ${__("Depósito")}: US$ ${remittanceMoney(preview.deposit_usd)}</strong></p>
            <p class="text-warning" data-selection-warning>${__("El total seleccionado no coincide con el depósito. No se repartirán ni reducirán importes automáticamente; deberá revisar la diferencia y los destinos antes de conciliar.")}</p>
            <p>${__(fromCollection ? "Este detalle procede de la cobranza cargada; no confirma una deducción informada por la empresa. Generarlo no modifica la cobranza ni confirma o concilia el depósito." : "Este detalle procede del core, no de una confirmación de deducción de la empresa. Generarlo no confirma ni concilia el depósito.")}</p>
            <p>${__(fromCollection ? "Marque las filas de este depósito y ajuste sus importes si son distintos. La cobranza original se muestra como referencia; los cambios se registrarán en los comentarios del detalle." : "Marque los movimientos de este depósito y ajuste sus importes si son distintos. El pendiente original se muestra como referencia; los ajustes se registrarán en los comentarios del detalle.")}</p>
            <p class="text-danger" data-amount-error>${__("Ingrese importes mayores que cero con un máximo de dos decimales en las filas seleccionadas.")}</p>
            <p data-selection-count aria-live="polite"></p>
            <div style="max-height:40vh;overflow:auto"><table class="table table-bordered"><thead>
                <tr><th><label><input type="checkbox" data-select-all checked> ${__("Todos")}</label></th><th>${__("Cliente")}</th><th>${__("Crédito")}</th><th>${__("Período")}</th><th>${__("Referencia")}</th><th>${__("Importe del detalle US$")}</th></tr></thead>
                <tbody>${preview.rows.map((row, index) => `<tr><td><input type="checkbox" data-application-index="${index}" checked aria-label="${esc(__("Seleccionar movimiento {0}", [index + 1]))}"></td><td>${esc(row.client_name)}<br>${esc(row.client_number)}</td>
                    <td>${esc(row.loan_number)}</td><td>${esc(row.period)}</td><td>${esc(row.application_reference)}</td><td>
                        <input type="number" class="form-control input-sm" style="min-width:120px" min="0.01" step="0.01" data-application-amount="${index}"
                            value="${esc(amounts.get(row.claim_id))}" aria-label="${esc(__("Importe del movimiento {0} en US$", [index + 1]))}">
                        <small class="text-muted">${__(fromCollection ? "Cobranza original" : "Pendiente original")}: ${remittanceMoney(row.deducted_usd)}</small></td></tr>`).join("")}</tbody></table></div>
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
                await frappe.call({method: method + (fromCollection ? "use_collection_detail" : "use_application_detail"),
                    args: {remittance_name: frm.doc.name, fingerprint: preview.fingerprint,
                        replace_detail: values.replace_detail || 0,
                        selected_claim_ids: JSON.stringify([...selected]),
                        selected_amounts: JSON.stringify(Object.fromEntries([...selected].map(key => [key, amounts.get(key)])))}, freeze: true,
                    freeze_message: __(fromCollection ? "Generando detalle desde cobranza…" : "Generando detalle desde aplicaciones…")});
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
    unreconcile_detail: unreconcileRemittanceDetail,
    create_client_alias: createRemittanceClientAlias,
    view_related_pending: showRemittanceRelatedPending,
    create_client_credit: createClientCreditFromDetail,
    create_company_credit: createCompanyCreditFromDetail,
    select_pending_targets: selectPendingTargetsForDetail,
    form_render(frm, cdt, cdn) {
        const row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
        const undoControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.unreconcile_detail;
        undoControl?.$wrapper.toggle(canUnreconcileRemittanceDetail(frm, row));
        const aliasControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.create_client_alias;
        aliasControl?.$wrapper.toggle(canCreateRemittanceClientAlias(frm, row));
        const control = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.create_client_credit;
        control?.$wrapper.toggle(canCreateDetailClientCredit(frm, row));
        const companyControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.create_company_credit;
        companyControl?.$wrapper.toggle(canCreateDetailClientCredit(frm, row) && canCreateCompanyCredit(frm));
        const targetControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.select_pending_targets;
        targetControl?.$wrapper.toggle(canSelectDetailPendingTargets(frm, row));
        const relatedControl = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[cdn]?.grid_form?.fields_dict?.view_related_pending;
        relatedControl?.$wrapper.toggle(canViewRemittanceRelatedPending(frm, row));
    },
    loan_number(frm) {
        frm.set_value("result", "Pendiente");
        frm.set_value("detail_status", "Cargado; pendiente de conciliación");
        frappe.show_alert({message: __("Guarde y use Conciliar. Revise los destinos manuales si cambió el crédito."), indicator: "orange"});
    },
});

function canCreateRemittanceClientAlias(frm, row) {
    return !frm.is_new() && frm.doc.docstatus !== 2 && !frm.doc.consolidated_deposit &&
        frm.doc.cash_identity_status !== "Origen vinculado" && !!frm.get_perm(0, "write") &&
        !!frappe.model?.can_write?.("CN Client") && !!row?.name && !row.client &&
        !!String(row.client_name || "").trim() &&
        (row.identity_reason || "").includes("no se pudo verificar Nombre y apellidos") &&
        [row.loan_number, row.client_number, row.employee_number].some(value => String(value || "").trim());
}

function canUnreconcileRemittanceDetail(frm, row) {
    return !frm.is_new() && frm.doc.docstatus === 1 && !!frm.get_perm(0, "write") && !!row?.name
        && row.row_kind !== "Diferencia documentada" && !frm.doc.consolidated_deposit
        && frm.doc.cash_identity_status !== "Origen vinculado";
}

function unreconcileRemittanceDetail(frm, cdt, cdn) {
    const row = (frm.doc.detail_rows || []).find(item => item.name === cdn);
    if (!canUnreconcileRemittanceDetail(frm, row) || frm._cnUnreconcilingRow) return;
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde o recargue el depósito antes de desconciliar una fila."));
    const name = frm.doc.name, modified = frm.doc.modified;
    frm._cnUnreconcilingRow = cdn;
    return frappe.confirm(__("¿Desconciliar la fila {0}? Sus destinos se retirarán y quedará pendiente de selección manual. Las demás filas conservarán su conciliación.", [row.source_row || row.idx]),
        async () => {
            try {
                if (frm.doc.name !== name || frm.doc.modified !== modified || frm.is_dirty()) {
                    return frappe.msgprint(__("El depósito cambió. Recargue antes de desconciliar."));
                }
                await frappe.call({method: "credinomina_reconciliation.remittance_row_reconciliation.unreconcile_row",
                    args: {remittance_name: name, detail_row_name: cdn, modified}, freeze: true,
                    freeze_message: __("Desconciliando la fila y actualizando saldos…")});
                if (frm.doc.name === name) await frm.reload_doc();
                frappe.show_alert({message: __("Fila pendiente de selección manual. Use Seleccionar partidas pendientes."), indicator: "orange"});
            } finally {frm._cnUnreconcilingRow = null;}
        }, () => {frm._cnUnreconcilingRow = null;});
}

function setupRemittanceClientAlias(frm) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const formatter = (value, df, options, row) => `${esc(value)}${row?.parent === frm.doc.name && canCreateRemittanceClientAlias(frm, row)
        ? `<br><a href="#" data-create-client-alias="${esc(row.name)}">${__("Crear alias")}</a>` : ""}`;
    const field = frappe.meta?.get_docfield?.("CN Remittance Detail", "client_name");
    if (field) field.formatter = formatter;
    frm.fields_dict.detail_rows?.grid?.update_docfield_property?.("client_name", "formatter", formatter);
    const wrapper = frm.fields_dict.detail_rows?.$wrapper;
    if (wrapper?.off && wrapper?.on) {
        wrapper.off("click.cnClientAlias", "[data-create-client-alias]").on("click.cnClientAlias", "[data-create-client-alias]", event => {
            event.preventDefault();
            event.stopPropagation();
            createRemittanceClientAlias(frm, "CN Remittance Detail", event.currentTarget.dataset.createClientAlias);
        });
    }
}

async function createRemittanceClientAlias(frm, cdt, cdn) {
    const row = (frm.doc.detail_rows || []).find(item => item.name === cdn);
    if (!canCreateRemittanceClientAlias(frm, row)) return;
    if (frm.clientAliasDialog) {frm.clientAliasDialog.show(); return;}
    if (frm.clientAliasOpening) return;
    frm.clientAliasOpening = true;
    const depositName = frm.doc.name;
    const api = "credinomina_reconciliation.remittance_client_alias.";
    try {
        if (frm.is_dirty()) await frm.save();
        if (frm.doc.name !== depositName) return;
        const response = await frappe.call({method: api + "preview_alias",
            args: {remittance_name: depositName, detail_row_name: cdn}});
        const data = response.message;
        let saving = false;
        const dialog = new frappe.ui.Dialog({title: __("Crear alias del cliente"), fields: [
            {fieldtype: "Link", fieldname: "client", label: __("Cliente verificado"), options: "CN Client", reqd: 1,
                default: data.client, get_query: () => ({filters: {name: ["in", [data.client]]}})},
            {fieldtype: "Data", fieldname: "client_name", label: __("Nombre registrado"), read_only: 1, default: data.client_name},
            {fieldtype: "Data", fieldname: "client_number", label: __("Nro. Cliente"), read_only: 1, default: data.client_number},
            {fieldtype: "Data", fieldname: "employee_number", label: __("Nro. Empleado"), read_only: 1, default: data.employee_number},
            {fieldtype: "Data", fieldname: "loan_number", label: __("Nro. Crédito de la fila"), read_only: 1, default: data.loan_number},
            {fieldtype: "Data", fieldname: "employer", label: __("Empresa del detalle"), read_only: 1, default: data.employer},
            {fieldtype: "Data", fieldname: "alias_name", label: __("Nombre importado que se agregará como alias"),
                read_only: 1, default: data.alias_name,
                description: __("Confirme que el nombre importado corresponde a esta persona. Después de guardar, use Conciliar para actualizar los destinos.")},
        ], primary_action_label: __("Guardar alias"), async primary_action(values) {
            if (saving) return;
            if (frm.doc.name !== depositName || frm.is_dirty()) {
                frappe.msgprint(__("El depósito cambió. Cierre el modal, guarde y vuelva a abrir Crear alias.")); return;
            }
            saving = true;
            dialog.disable_primary_action();
            try {
                await frappe.call({method: api + "create_alias", type: "POST", freeze: true, args: {
                    remittance_name: depositName, detail_row_name: cdn, client_name: values.client,
                    modified: data.modified, client_modified: data.client_modified}});
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __("Alias guardado y cliente identificado. Use Conciliar para actualizar los destinos."), indicator: "green"});
            } finally {saving = false; dialog.enable_primary_action();}
        }, onhide() {if (frm.clientAliasDialog === dialog) frm.clientAliasDialog = null;}});
        frm.clientAliasDialog = dialog;
        dialog.show();
    } finally {frm.clientAliasOpening = false;}
}

function canViewRemittanceRelatedPending(frm, row) {
    if (row?.row_kind === "Diferencia documentada") return false;
    const name = row?.client_name && row.client_name !== "Sin identificar" ? row.client_name : "";
    return !frm.is_new() && frm.doc.docstatus !== 2 && !!row?.name &&
        !!(row.employer || frm.doc.employer) &&
        [row.client, row.client_number, row.national_id, name].some(value => String(value || "").trim());
}

async function showRemittanceRelatedPending(frm, cdt, cdn) {
    const row = (frm.doc.detail_rows || []).find(item => item.name === cdn);
    if (!canViewRemittanceRelatedPending(frm, row)) {
        frappe.msgprint(__("Identifique al cliente de esta fila y guarde el depósito antes de consultar sus pendientes."));
        return;
    }
    if (frm.is_dirty()) {
        frappe.msgprint(__("Guarde el depósito antes de consultar los registros relacionados."));
        return;
    }
    if (frm.relatedPendingOpening) return;
    frm.relatedPendingOpening = true;
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.accounting_related_records.get_related_pending_for_remittance",
            args: {remittance_name: frm.doc.name, detail_row_name: cdn},
        });
        await frappe.require("/assets/credinomina_reconciliation/js/related_pending_modal.js");
        window.credinominaShowRelatedPending(response.message || {});
    } catch (error) {
        frappe.msgprint(__("No se pudieron consultar los registros relacionados. Revise sus permisos o vuelva a intentar."));
    } finally {
        frm.relatedPendingOpening = false;
    }
}

function canSelectDetailPendingTargets(frm, row) {
    if (row?.row_kind === "Diferencia documentada") return false;
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
    return !!row && !frm.is_new() && frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write");
}

function creditCommitmentDateDefault(postingDate = frappe.datetime.get_today()) {
    const [year, month] = (postingDate || frappe.datetime.get_today()).split("-").map(Number);
    return new Date(Date.UTC(year, month + 1, 0)).toISOString().slice(0, 10);
}

function addBulkClientCreditButton(frm, attempt = 0) {
    const grid = frm.fields_dict.detail_rows?.grid;
    // Frappe creates these controls when the grid renders, after form setup.
    if (!grid?.add_custom_button || !grid.get_selected_children) return;
    if (!grid.custom_buttons || !grid.grid_buttons || !grid.wrapper) {
        // The form's refresh can run before a lazy child-table grid is rendered.
        // Retry briefly so the action is installed as soon as its footer exists.
        if (attempt < 10 && !frm.bulkClientCreditButtonRetryScheduled) {
            frm.bulkClientCreditButtonRetryScheduled = true;
            setTimeout(() => {
                frm.bulkClientCreditButtonRetryScheduled = false;
                addBulkClientCreditButton(frm, attempt + 1);
            }, 50);
        }
        return;
    }
    const label = __("Documentar diferencia seleccionada");
    frm.bulkClientCreditButton = grid.add_custom_button(label, () => createBulkClientCredits(frm), "bottom");
    const refresh = () => {
        const selected = grid.get_selected_children();
        const available = selected.length >= 1 && selected.every(row => canCreateDetailClientCredit(frm, row));
        frm.bulkClientCreditButton.toggleClass("hidden", !available);
    };
    grid.wrapper.off("change.cnBulkClientCredits", ".grid-row-check")
        .on("change.cnBulkClientCredits", ".grid-row-check", () => setTimeout(refresh, 0));
    refresh();
}

async function createBulkClientCredits(frm) {
    const rows = frm.fields_dict.detail_rows?.grid?.get_selected_children?.() || [];
    if (rows.length !== 1) {
        frappe.msgprint(__("Seleccione una fila para documentar su diferencia y soporte individualmente."));
        return;
    }
    return createRemittanceComplementary(frm, {detailRowName: rows[0].name});
}

async function createClientCreditFromDetail(frm, cdt, cdn) {
    if (frm.detail_credit_workflow) return;
    const row = (frm.doc.detail_rows || []).find(row => row.name === cdn);
    if (!canCreateDetailClientCredit(frm, row)) {
        frappe.msgprint(__("Seleccione una fila de un depósito vigente con permiso de escritura."));
        return;
    }
    frm.detail_credit_workflow = true;
    try {
        await createRemittanceComplementary(frm, {detailRowName: row.name});
    } finally {
        frm.detail_credit_workflow = false;
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
    return false;
}

function addCompanyCreditButton(frm) {
    if (canCreateCompanyCredit(frm)) {
        frm.add_custom_button(__("Clasificar / documentar diferencia"), () => createCompanyCreditFromDeposit(frm));
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
    if (frm.is_new() || frm.is_dirty()) {
        frappe.msgprint(__("Guarde el documento antes de documentar la diferencia."));
        return;
    }
    const detailRow = (frm.doc.detail_rows || []).find(row => row.name === options.detailRowName);
    if (options.fromDetailDifference || detailRow?.row_kind === "Diferencia documentada") {
        return documentRemittanceCompanyDifference(frm);
    }
    return credinomina.document_difference({doctype: frm.doc.doctype, name: frm.doc.name,
        parentfield: options.detailRowName ? "detail_rows" : "", row_name: options.detailRowName || "",
        on_saved: () => frm.reload_doc()});
}

async function reviewRemittanceDifferences(frm) {
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde o recargue el depósito antes de documentar sus diferencias."));
    const existing = frm._cn_difference_helper;
    if (existing) {
        if (existing.deposit_name !== frm.doc.name) return frappe.msgprint(__("Cierre el helper del depósito anterior antes de abrir otro."));
        if (existing.is_minimized) existing.toggle_minimize();
        else if (!existing.display) existing.show();
        return existing;
    }
    if (frm._cn_difference_helper_loading) return;
    const name = frm.doc.name, method = "credinomina_reconciliation.deposit_difference_helper.";
    frm._cn_difference_helper_loading = true;
    try {
        const {message: data} = await frappe.call({method: method + "load", args: {name}});
        if (frm.doc.name !== name || frm.is_dirty()) return frappe.msgprint(__("El formulario cambió. Abra nuevamente el helper."));
        if (String(frm.doc.modified) !== data.modified) return frappe.msgprint(__("El depósito tiene cambios más recientes. Recárguelo antes de abrir el helper."));
        if (!data.rows.length) return frappe.msgprint(__("No hay filas con diferencias, saldos a favor o documentación previa."));
        let modified = data.modified, busy = false;
        const baseline = new Map(data.rows.map(row => [row.row_name, {
            difference_reason: row.difference_reason || "", difference_notes: row.difference_notes || "",
        }]));
        const dialog = new frappe.ui.Dialog({
            title: __("Documentar diferencias del detalle"), size: "extra-large", static: true, minimizable: true,
            on_minimize_toggle(minimized) {
                const label = minimized ? __("Restaurar diferencias del detalle") : __("Minimizar diferencias del detalle");
                this.get_minimize_btn().attr({title: label, "aria-label": label});
            },
            onhide() { if (frm._cn_difference_helper === this) frm._cn_difference_helper = null; },
            fields: [
                {fieldtype: "HTML", options: `<p>${__("Edite la razón y los comentarios de las filas con pendiente, saldo a favor o documentación previa. Los comentarios son opcionales. Guardar conserva los importes, vínculos y estados de conciliación; no necesita volver a conciliar.")}</p>`},
                {fieldname: "rows", fieldtype: "Table", label: __("Diferencias del detalle"),
                    cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
                    data: data.rows.map(row => ({...row})), fields: [
                        {fieldname: "row_name", fieldtype: "Data", hidden: 1, read_only: 1},
                        {fieldname: "source_row", fieldtype: "Int", label: __("Fila de origen"), read_only: 1},
                        {fieldname: "client_name", fieldtype: "Data", label: __("Cliente / empresa"), read_only: 1, in_list_view: 1, columns: 2},
                        {fieldname: "loan_number", fieldtype: "Data", label: __("Crédito"), read_only: 1, in_list_view: 1, columns: 1},
                        {fieldname: "employer", fieldtype: "Data", label: __("Empresa"), read_only: 1},
                        {fieldname: "row_kind", fieldtype: "Data", label: __("Tipo de fila"), read_only: 1},
                        {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Importe US$"), read_only: 1, in_list_view: 1, columns: 1},
                        {fieldname: "linked_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Vinculado US$"), read_only: 1, in_list_view: 1, columns: 1},
                        {fieldname: "pending_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Pendiente US$"), read_only: 1, in_list_view: 1, columns: 1},
                        {fieldname: "difference_reason", fieldtype: "Link", options: "CN Difference Reason", label: __("Razón de diferencia"), in_list_view: 1, columns: 2},
                        {fieldname: "difference_notes", fieldtype: "Small Text", label: __("Comentarios de la diferencia"), in_list_view: 1, columns: 2},
                        {fieldname: "match_status", fieldtype: "Data", label: __("Estado"), read_only: 1},
                        {fieldname: "match_reason", fieldtype: "Small Text", label: __("Motivo de conciliación"), read_only: 1},
                    ]},
            ],
            primary_action_label: __("Guardar diferencias"),
            async primary_action() {
                if (busy) return;
                if (frm.is_dirty()) return frappe.msgprint(__("Guarde o recargue los cambios del depósito antes de guardar el helper. Sus ediciones permanecen aquí."));
                const changes = dialog.fields_dict.rows.grid.get_data().map(row => ({row_name: row.row_name,
                    difference_reason: String(row.difference_reason || "").trim(), difference_notes: String(row.difference_notes || "").trim(),
                })).filter(row => {
                    const saved = baseline.get(row.row_name);
                    return saved && (row.difference_reason !== saved.difference_reason || row.difference_notes !== saved.difference_notes);
                });
                if (!changes.length) return frappe.show_alert({message: __("No hay cambios para guardar."), indicator: "blue"});
                if (changes.some(row => !row.difference_reason)) return frappe.msgprint(__("Seleccione una razón de diferencia para cada fila modificada."));
                busy = true; dialog.disable_primary_action(); dialog.get_close_btn().prop("disabled", true);
                try {
                    const requestVersion = modified;
                    const response = (await frappe.call({method: method + "save", args: {name, modified, rows: changes}})).message;
                    modified = response.modified;
                    for (const row of response.rows) baseline.set(row.row_name, {
                        difference_reason: row.difference_reason || "", difference_notes: row.difference_notes || "",
                    });
                    if (frm.doc.name === name && !frm.is_dirty() && String(frm.doc.modified) === requestVersion) {
                        for (const saved of response.rows) {
                            const row = (frm.doc.detail_rows || []).find(item => item.name === saved.row_name);
                            if (!row) continue;
                            row.difference_reason = saved.difference_reason; row.difference_notes = saved.difference_notes;
                            const gridRow = frm.fields_dict.detail_rows?.grid?.grid_rows_by_docname?.[row.name];
                            gridRow?.refresh_field("difference_reason"); gridRow?.refresh_field("difference_notes");
                        }
                        frm.doc.modified = response.modified;
                    }
                    dialog.set_alert(__("Diferencias guardadas. Puede seguir editando o cerrar; la conciliación se conserva."), "success");
                } finally {busy = false; dialog.enable_primary_action(); dialog.get_close_btn().prop("disabled", false);}
            },
            secondary_action_label: __("Cerrar"), secondary_action() { if (!busy) dialog.hide(); },
        });
        dialog.deposit_name = name;
        // Static dialogs disable outside-click and Escape dismissal. Restore
        // the explicit X that Frappe normally hides for static dialogs.
        dialog.get_close_btn().show();
        dialog.get_minimize_btn().attr({title: __("Minimizar diferencias del detalle"), "aria-label": __("Minimizar diferencias del detalle")});
        frm._cn_difference_helper = dialog;
        dialog.show();
        return dialog;
    } finally {frm._cn_difference_helper_loading = false;}
}

async function documentRemittanceCompanyDifference(frm) {
    const method = "credinomina_reconciliation.deposit_difference.";
    const response = await frappe.call({method: method + "preview", args: {name: frm.doc.name}});
    const preview = response.message;
    let saving = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Diferencia del detalle del depósito"), size: "large",
        fields: [
            {fieldname: "information", fieldtype: "HTML", options: `<p>${__("Se registrará una fila de diferencia a nombre de la empresa. Los importes originales se conservan.")}</p>
                <p>${__("Si falta efectivo, se distribuye proporcionalmente entre las filas de clientes, redondeando a centavos. El faltante conserva su saldo; documentarlo no lo convierte en un pago.")}</p>
                <p>${__("Puede vincular una partida importada del core ahora o más adelante. Debe corresponder a la misma empresa, importe y signo. Después de guardar, use Conciliar.")}</p>`},
            {fieldname: "employer", fieldtype: "Data", label: __("Empresa"), read_only: 1, default: preview.employer},
            {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", label: __("Diferencia (depósito − detalle) US$"), read_only: 1, default: preview.amount_usd},
            {fieldname: "difference_reason", fieldtype: "Link", options: "CN Difference Reason", label: __("Razón de la diferencia"), reqd: 1, default: preview.difference_reason},
            {fieldname: "difference_notes", fieldtype: "Small Text", label: __("Comentarios de la diferencia"), default: preview.difference_notes},
            {fieldname: "core_item", fieldtype: "Link", options: "CN Complementary Item", label: __("Partida del core (opcional)"), default: preview.core_item,
                get_query: () => ({filters: {docstatus: 1, employer: preview.employer, accounting_source_key: ["is", "set"], amount_usd: preview.amount_usd}})},
        ],
        primary_action_label: __("Guardar diferencia"),
        secondary_action_label: preview.has_difference ? __("Retirar diferencia") : undefined,
        async secondary_action() {
            if (saving || !preview.has_difference) return;
            saving = true;
            try {
                await frappe.call({method: method + "record", args: {name: frm.doc.name, modified: preview.modified, remove: 1}});
                dialog.hide(); await frm.reload_doc();
            } finally {saving = false;}
        },
        async primary_action(values) {
            if (saving) return;
            saving = true; dialog.disable_primary_action();
            try {
                await frappe.call({method: method + "record", args: {name: frm.doc.name, modified: preview.modified,
                    difference_reason: values.difference_reason, difference_notes: values.difference_notes || "",
                    core_item: values.core_item || ""}});
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __("Diferencia guardada. Use Conciliar para actualizar la distribución."), indicator: "green"});
            } finally {saving = false; dialog.enable_primary_action();}
        },
    });
    dialog.show();
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
            destination: targetDisplayLabel(target, frm.doc.detail_rows || []),
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

async function showRemittanceComplementaryItems(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Guarde o recargue el depósito antes de consultar sus partidas complementarias."));
        return;
    }
    const name = frm.doc.name;
    const {message: items} = await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.get_linked_complementary_items",
        args: {remittance_name: name},
        freeze: true,
        freeze_message: __("Consultando partidas vinculadas al depósito…"),
    });
    if (frm.doc.name !== name) return;
    if (!items?.length) {
        frappe.msgprint(__("Este depósito no tiene partidas complementarias vinculadas que pueda consultar."));
        return;
    }
    frappe.set_route("List", "CN Complementary Item", {name: ["in", items]});
}

async function refreshRemittanceCreditStatus(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Guarde o recargue el depósito antes de actualizar los estados de crédito."));
        return;
    }
    if (frm._cnRefreshingCreditStatus) return;
    const name = frm.doc.name, modified = frm.doc.modified;
    frm._cnRefreshingCreditStatus = true;
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.remittance_portfolio.refresh_credit_status",
            args: {remittance_name: name, modified}, freeze: true,
            freeze_message: __("Actualizando estados del crédito desde cartera…"),
        });
        if (frm.doc.name === name) await frm.reload_doc();
        const result = response.message || {};
        frappe.show_alert({message: __("Estados actualizados: {0}. Sin identificar: {1}.",
            [result.updated || 0, result.unidentified || 0]), indicator: result.unidentified ? "orange" : "green"});
    } finally {
        frm._cnRefreshingCreditStatus = false;
    }
}

function remittanceCashAmounts(doc) {
    const cents = value => Math.round(Number(value || 0) * 100);
    const total = cents(doc.amount_usd), assigned = cents(doc.allocated_usd), credit = cents(doc.justified_surplus_usd);
    return {assigned: assigned / 100, credit: credit / 100, pending: (total - assigned - credit) / 100};
}

function remittanceFinancialCards(doc) {
    const cash = remittanceCashAmounts(doc);
    let complementary = null;
    try {
        const entries = JSON.parse(doc.allocation_detail || "[]");
        if (!Array.isArray(entries) || entries.some(row => !row || typeof row !== "object") || (cash.assigned && !entries.length)) throw new Error("Invalid journal");
        complementary = entries.filter(row => row.tipo === "Partida complementaria")
            .reduce((sum, row) => sum + Math.round(Number(row.importe_usd || 0) * 100), 0) / 100;
    } catch (_) { /* Unknown evidence is not a zero distribution. */ }
    return {credits: complementary == null ? null : Math.round((cash.assigned - complementary) * 100) / 100,
        complementary: complementary == null ? null : Math.round((complementary + cash.credit) * 100) / 100,
        pending: cash.pending, credit: cash.credit};
}

function remittanceNextAction(frm) {
    if (frm.doc.docstatus === 2 || !frm.get_perm(0, "write")) return null;
    if (frm.is_new() || frm.is_dirty()) return {label: "Guardar cambios", run: () => frm.save()};
    if (frm.doc.docstatus === 0) return frm.get_perm(0, "submit") ? {label: "Confirmar depósito", run: () => frm.savesubmit()} : null;
    const presentation = remittancePresentation(frm.doc);
    if (presentation.reconciled) return {label: "Ver distribución", run: () => frm.scroll_to_field("complete_distribution")};
    if (!(frm.doc.detail_rows || []).length && !(frm.doc.targets || []).length) {
        const hasFile = /\.(xlsx|xls|csv)(\?|$)/i.test(frm.doc.detail_file || frm.doc.support_file || "");
        return hasFile ? {label: "Cargar detalle", run: () => importRemittanceDetail(frm)}
            : {label: "Agregar detalle o destinos", run: () => frm.scroll_to_field("detail_file")};
    }
    if ((frm.doc.detail_status || "").startsWith("Revisar") || ["Revisar destinos", "Revisar detalle"].includes(frm.doc.result)) {
        return {label: "Resolver diferencias", run: () => frm.scroll_to_field("detail_rows")};
    }
    return {label: "Conciliar", run: () => reconcileRemittance(frm)};
}

function remittancePresentation(doc, dirty = false) {
    const registration = doc.docstatus === 2 ? "Cancelado" : doc.docstatus === 1 ? "Confirmado" : "Borrador";
    const cash = remittanceCashAmounts(doc);
    const financialReview = !Number.isFinite(cash.pending) || cash.pending !== 0 || cash.credit < 0
        || Number(doc.allocated_usd || 0) < 0 || Number(doc.unclassified_usd || 0) !== 0
        || doc.amount_usd != null && Number(doc.amount_usd) <= 0;
    const recordedSettlement = ["Conciliado", "Conciliado con saldo a favor", "Conciliado con saldo a favor del cliente"].includes(doc.result)
        || cash.credit > 0 && ["Parcial con saldo a favor", "Saldo a favor documentado"].includes(doc.result);
    const detailReview = (doc.detail_status || "").startsWith("Revisar");
    const documented = doc.result === "Conciliado con diferencia documentada" && doc.detail_status === doc.result;
    const reconciled = doc.docstatus === 1 && !detailReview && (recordedSettlement && !financialReview || documented);
    const review = doc.docstatus === 1 && (detailReview ||
        ["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(doc.result) || recordedSettlement && financialReview);
    let hint = "Depósito en borrador: todavía no participa en la conciliación. Complete los datos, guarde y confirme el depósito.";
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
        registration, hint, reconciled,
        result: doc.docstatus === 2 ? "No participa" : doc.docstatus !== 1 ? "Sin conciliar"
            : review && recordedSettlement ? "Por revisar" : reconciled && cash.credit > 0 ? "Conciliado con saldo a favor" : doc.result || "Pendiente",
        color: doc.docstatus === 2 ? "gray" : dirty ? "orange" : reconciled ? "green" : review ? "orange" : "blue",
    };
}

function remittanceDetailDifference(doc) {
    const rows = doc.detail_rows || [];
    if (!rows.length || doc.docstatus === 2) return null;
    if (!Number.isFinite(Number(doc.amount_usd)) || Number(doc.amount_usd) <= 0) {
        return {error: __("Complete el importe y la moneda del depósito para compararlo con su detalle.")};
    }
    const rate = toScaledInteger(Number(doc.fx_rate || 0).toFixed(8), 8);
    let total = 0n;
    for (const row of rows) {
        if (row.row_kind === "Diferencia documentada") continue;
        const usd = toScaledInteger(row.deducted_usd, 2), nio = toScaledInteger(row.deducted_nio, 2);
        if (usd < 0n || nio < 0n) return {error: __("Revise los importes negativos de la fila {0} del detalle.", [row.idx || row.source_row || "—"])};
        if (usd > 0n) total += usd;
        else if (nio > 0n) {
            if (rate <= 0n) return {error: __("Complete la tasa C$/US$ para comparar el depósito con el detalle.")};
            total += (nio * 100000000n + rate / 2n) / rate;
        } else if (row.deducted_usd == null && row.deducted_nio == null) {
            const amount = toScaledInteger(row.amount_usd, 2);
            if (amount < 0n) return {error: __("Revise el importe de la fila {0} del detalle.", [row.idx || row.source_row || "—"])};
            total += amount;
        }
    }
    const deposit = toScaledInteger(doc.amount_usd, 2);
    return {deposit: Number(deposit) / 100, detail: Number(total) / 100, difference: Number(deposit - total) / 100};
}

function remittanceDifferenceTargets(doc, difference) {
    const targets = (doc.targets || []).filter(row => row.complementary_item && !row.detail_row
        && (!row.result || ["Pendiente", "Aplicada"].includes(row.result)));
    const selected = targets.reduce((sum, row) => sum + toScaledInteger(row.amount_usd, 2), 0n);
    return difference && selected === toScaledInteger(difference, 2) ? targets : [];
}

function remittanceDetailDifferenceHtml(doc, interactive = false, canReconcile = false) {
    const recorded = (doc.detail_rows || []).find(row => row.row_kind === "Diferencia documentada");
    if (!recorded && remittancePresentation(doc).reconciled) return "";
    const data = remittanceDetailDifference(doc);
    if (!data || !recorded && !data.error && !data.difference) return "";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    if (data.error) return `<div class="alert alert-warning" role="status">${esc(data.error)}</div>`;
    const signed = `${data.difference > 0 ? "+" : "−"}US$ ${remittanceMoney(Math.abs(data.difference))}`;
    const selected = remittanceDifferenceTargets(doc, data.difference);
    const links = [...new Set(selected.map(row => row.complementary_item))].map(name =>
        `<a href="/app/cn-complementary-item/${encodeURIComponent(name)}" target="_blank" rel="noopener noreferrer">${esc(name)}</a>`).join(", ");
    return `<div class="alert ${selected.length ? "alert-info" : "alert-warning"}" style="margin-top:12px" role="status">
        <strong>${esc(__(selected.length ? "Diferencia con partidas vinculadas; pendiente de conciliar" : "El importe del depósito es diferente al detalle"))}</strong>
        <div>${esc(__("Depósito"))}: US$ ${esc(remittanceMoney(data.deposit))}${doc.deposit_currency === "NIO" ? ` (C$ ${esc(remittanceMoney(doc.deposit_amount))})` : ""}</div>
        <div>${esc(__("Detalle del depósito"))}: US$ ${esc(remittanceMoney(data.detail))}</div>
        <div><strong>${esc(__("Diferencia (depósito − detalle)"))}: ${esc(signed)}</strong></div>
        ${recorded ? `<div>${esc(__("Fila de empresa"))}: ${esc(recorded.client_name)} · ${esc(recorded.difference_reason)} · ${esc(recorded.difference_core_status || "Pendiente de respaldo del core")}</div>
            <div>${esc(__("Pendiente de respaldo del core"))}: US$ ${esc(remittanceMoney(recorded.difference_core_pending_usd))}</div>` : ""}
        ${selected.length ? `<p class="small mt-2">${esc(__("Partidas seleccionadas"))}: ${links}.</p>
            <p class="small">${esc(__("La diferencia ya está indicada en Destinos. Guarde y use Conciliar para validar y aplicar la distribución."))}</p>`
            : `<p class="small mt-2">${esc(__("Positiva: depósito mayor al detalle. Negativa: depósito menor al detalle. Revise el origen de la diferencia y las partidas existentes antes de documentarla."))}</p>`}
        ${(selected.length || recorded) && canReconcile && doc.docstatus === 1 ? `<button type="button" class="btn btn-primary btn-sm cn-reconcile-detail-difference">${esc(__("Conciliar depósito"))}</button>` : ""}
        ${interactive ? `<button type="button" class="btn btn-default btn-sm cn-create-detail-difference">${esc(__("Clasificar / documentar diferencia"))}</button>` : ""}
    </div>`;
}

function remittanceMoney(value) {
    const amount = Number(value ?? 0);
    return Number.isFinite(amount) ? amount.toLocaleString("es-NI", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}

function setupRemittanceLinkedTotal(frm) {
    const grid = frm.fields_dict.detail_rows?.grid;
    const formatter = (value, df, options, row) => frappe.format(row?.linked_total_usd ?? 0,
        {fieldtype: "Currency", options: "usd_currency", precision: 2}, null, {...row, usd_currency: "USD"});
    const field = frappe.meta?.get_docfield?.("CN Remittance Detail", "linked_total_usd");
    if (field) field.formatter = formatter;
    grid?.update_docfield_property?.("linked_total_usd", "formatter", formatter);
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
        if (row.row_kind !== "Diferencia documentada") {
            let matched = [];
            try { matched = JSON.parse(row.matched_targets || "[]"); } catch (_) { /* No valid automatic links. */ }
            const known = Array.isArray(matched) ? matched.filter(entry => entry && typeof entry === "object") : [];
            const internal = known.filter(entry => String(entry.instruction_id || "").startsWith("RECLASS:"));
            const entries = (manual.get(row.name) || known.filter(entry => !entry.instruction_id)).concat(internal);
            const linked = entries.reduce((sum, entry) => sum + cents(entry.amount_usd), 0);
            const pending = cents(row.amount_usd) - linked - cents(row.client_credit_usd) - cents(row.company_credit_usd);
            if (row.linked_usd !== linked / 100 || row.pending_usd !== pending / 100) {
                row.linked_usd = linked / 100;
                row.pending_usd = pending / 100;
                changed = true;
            }
        }
        // Virtual presentation only. The underlying destination and credit
        // amounts remain separate so a balance is never paid or counted twice.
        row.linked_total_usd = (cents(row.linked_usd) + cents(row.client_credit_usd) + cents(row.company_credit_usd)) / 100;
        frm.fields_dict?.detail_rows?.grid?.grid_rows_by_docname?.[row.name]?.refresh_field?.("linked_total_usd");
    }
    if (changed && frm.refresh_field) frm.refresh_field("detail_rows");
}

function remittanceIssues(doc, dirty = false) {
    if (doc.docstatus === 2) return [];
    const issues = [];
    const add = (text, field = "detail_rows") => issues.push({text, field});
    if (dirty) add("Hay cambios sin guardar. Guarde y use Conciliar para actualizar los motivos mostrados.", "targets");
    if (doc.docstatus !== 1) add("Confirme el depósito para que pueda participar en la conciliación.", "deposit_amount");
    const rows = doc.detail_rows || [], targets = doc.targets || [];
    if (!rows.length && !targets.length) add("Cargue el detalle por cliente o seleccione destinos para explicar el depósito.", "detail_file");
    if (doc.apply_fifo && rows.length && !(doc.detail_periods || []).some(row => row.period)) {
        add("FIFO necesita al menos un período seleccionado en Períodos a conciliar.", "detail_periods");
    }
    for (const row of rows) {
        if (["Conciliada", "No deducido", "Cobertura parcial; diferencia de empresa", "Conciliada con diferencia documentada", "Diferencia documentada"].includes(row.match_status)) continue;
        const label = [`Fila ${row.source_row || row.idx || "—"}`, row.client_name,
            row.loan_number && `Crédito ${row.loan_number}`].filter(Boolean).join(" · ");
        add(`${label}: ${row.match_reason || "Pendiente de comprobar. Guarde y pulse Conciliar para obtener el resultado de esta fila."}`);
    }
    const hints = {
        "Falta deposito o cobranza": "El destino no está disponible como partida vigente. Revise que esté confirmado, tenga período y conserve saldo.",
        "Empresa no autorizada": "La empresa del destino no está autorizada para este depósito. Revise empresa pagadora y beneficiaria.",
        "Importe invalido": "Revise el importe del destino; debe ser válido para el tipo de movimiento.",
        "Excede deposito": "El importe del destino supera el efectivo disponible del depósito. Revise las otras asignaciones y los saldos a favor reservados.",
        "Excede cobranza": "El importe supera el pendiente disponible de la aplicación o partida. Revise pagos y reservas existentes.",
        "Excede cobranza o partida complementaria": "La suma asignada supera el pendiente del destino. Revise importes y otras asignaciones.",
    };
    for (const target of targets) {
        const label = target.complementary_item || target.historical_application || [target.period, target.row_key].filter(Boolean).join(" / ") || "sin seleccionar";
        if (!target.complementary_item && !target.historical_application && !(target.period && target.row_key)) {
            add(`Destino ${target.idx || "—"}: seleccione una aplicación o partida válida.`, "targets");
        } else if (target.result && target.result !== "Aplicada") {
            add(`Destino ${label}: ${hints[target.result] || target.result}`, "targets");
        }
    }
    const cash = remittanceCashAmounts(doc);
    if (doc.docstatus === 1 && doc.result !== "Conciliado con diferencia documentada" && Number.isFinite(cash.pending) && cash.pending !== 0) {
        add(cash.pending > 0
            ? `Quedan US$ ${remittanceMoney(cash.pending)} sin distribuir. Complete los destinos o documente el excedente según su tratamiento real.`
            : `La distribución supera el depósito en US$ ${remittanceMoney(-cash.pending)}. Revise los importes asignados.`, "targets");
    }
    if (!issues.length && !remittancePresentation(doc).reconciled && doc.docstatus === 1) {
        add("Pulse Conciliar para actualizar la validación y el estado del depósito.", "targets");
    }
    return issues;
}

function remittanceIssuesHtml(doc, dirty = false, interactive = false) {
    const issues = remittanceIssues(doc, dirty);
    if (!issues.length) return "";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    return `<div class="alert alert-warning" style="margin-top:16px"><strong>${esc(__("Qué falta para conciliar"))}</strong>
        <p class="small">${esc(__("Motivos de la última conciliación. Al corregir los datos, guarde y vuelva a Conciliar."))}</p>
        <ul style="padding-left:20px;margin-bottom:0">${issues.slice(0, 25).map(issue => `<li style="margin-bottom:6px">${esc(issue.text)}
            ${interactive ? `<button type="button" class="btn btn-default btn-xs" data-cn-issue-field="${esc(issue.field)}">${esc(__("Revisar"))}</button>` : ""}</li>`).join("")}</ul>
        ${issues.length > 25 ? `<p>${esc(__("Hay {0} motivos adicionales. Consulte las filas del detalle y sus destinos.", [issues.length - 25]))}</p>` : ""}</div>`;
}

function renderRemittanceOverview(frm) {
    updateDetailPendingAmounts(frm);
    if (!frm.dashboard || !frm.$wrapper) return;
    const escape = value => frappe.utils.escape_html(String(value || ""));
    const state = remittancePresentation(frm.doc, frm.is_dirty());
    const confirmed = frm.doc.docstatus === 1;
    const cash = remittanceCashAmounts(frm.doc);
    const financial = remittanceFinancialCards(frm.doc);
    const next = remittanceNextAction(frm);
    const shown = value => confirmed && value != null ? `US$ ${remittanceMoney(value)}` : "—";
    const cards = [
        ["Depositado US$", `US$ ${remittanceMoney(frm.doc.amount_usd)}`],
        ["Aplicado a créditos US$", shown(financial.credits)],
        ["Partidas complementarias US$", shown(financial.complementary)],
        ["Sin distribuir US$", shown(financial.pending)],
    ];
    const html = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <strong>${escape(frm.doc.employer || "Nuevo depósito")}</strong>
        <span>${escape(state.registration)} · <span class="indicator-pill ${state.color}">${escape(state.result)}</span></span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:12px">
        ${cards.map(([label, value]) => `<div style="padding:12px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg)">
            <div class="small text-muted">${escape(label)}</div><div style="font-size:16px;font-weight:600;margin-top:4px">${escape(value)}</div>
        </div>`).join("")}
    </div><p class="text-muted" style="margin:12px 0 0">${escape(__("Última conciliación: depósito = créditos + partidas complementarias + sin distribuir. Las partidas incluyen saldos a favor documentados."))}
        ${confirmed && cash.credit ? `${escape(__("Saldo a favor documentado"))}: US$ ${remittanceMoney(cash.credit)}.` : ""}</p>
    <p>${escape(__("Estado del detalle"))}: ${escape(frm.doc.detail_status || __("Sin detalle cargado"))}</p>
    ${remittanceDetailDifferenceHtml(frm.doc, frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write")
        ,
        confirmed && !!frm.get_perm(0, "write"))}
    ${state.result !== frm.doc.result && confirmed ? `<details class="text-muted"><summary>${escape(__("Resultado guardado (auditoría)"))}</summary><p>${escape(frm.doc.result || __("Pendiente"))}. ${escape(__("Se conserva el resultado registrado; el estado mostrado arriba considera la distribución y los saldos a favor documentados."))}</p></details>` : ""}
    <p class="text-muted" style="margin:12px 0 0" role="status">${escape(state.hint)}</p>
    ${next ? `<button type="button" class="btn btn-primary btn-sm cn-deposit-next">${escape(__(next.label))}</button>` : ""}`;
    const existing = frm.$wrapper.find(".cn-remittance-overview");
    if (existing.length) existing.html(html);
    else frm.dashboard.add_section(`<div class="cn-remittance-overview">${html}</div>`);
    frm.$wrapper.off("click.cn_deposit_next", ".cn-deposit-next").on("click.cn_deposit_next", ".cn-deposit-next", async function () {
        const action = remittanceNextAction(frm);
        if (!action || this.disabled) return;
        this.disabled = true;
        try {await action.run();} finally {this.disabled = false;}
    });
    frm.$wrapper.off("click.cn_reconcile_difference", ".cn-reconcile-detail-difference")
        .on("click.cn_reconcile_difference", ".cn-reconcile-detail-difference", async function () {
            if (this.disabled || frm.reconciliation_running || frm.doc.docstatus !== 1 || !frm.get_perm(0, "write")) return;
            this.disabled = true;
            try { await reconcileRemittance(frm); }
            finally { this.disabled = false; }
        });
    frm.$wrapper.off("click.cn_detail_difference", ".cn-create-detail-difference")
        .on("click.cn_detail_difference", ".cn-create-detail-difference", async function () {
            if (this.disabled || frm._detail_difference_workflow) return;
            frm._detail_difference_workflow = true;
            this.disabled = true;
            try { await createRemittanceComplementary(frm, {fromDetailDifference: true}); }
            finally { frm._detail_difference_workflow = false; this.disabled = false; }
        });
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

async function openWorkbenchDepositSelection(frm, result) {
    const detailName = result.source.row_name || "";
    const detail = (frm.doc.detail_rows || []).find(row => row.name === detailName);
    if (detailName && !detail) return frappe.msgprint(__("La fila cambió. Actualice la mesa de conciliación."));
    const data = await loadPendingRemittanceTargets(frm, detailName, [JSON.stringify(["H", result.application.row_name])]);
    const candidate = data.rows.find(row => row.historical_application === result.application.row_name);
    if (!candidate) return frappe.msgprint(__("La aplicación ya no está disponible. Actualice la mesa de conciliación."));
    const picker = new RemittanceTargetPicker(frm, data, detail);
    const cents = Number(toScaledInteger(result.proposed_usd, 2));
    if (cents > 0 && cents <= candidate.pending_cents && cents <= picker.available_cents) {
        picker.selected.set(candidate.id, cents);
        picker.render();
    } else {
        frappe.msgprint(__("El disponible cambió. Revise el importe en el selector antes de agregar el destino."));
    }
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
            Los resultados se ordenan por fecha del período, del más antiguo al más reciente; las partidas sin período quedan al final.
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
                <th class="text-right text-nowrap" title="Saldo disponible de aplicaciones efectivas y partidas complementarias, descontando la cobertura ya asignada">Pendiente US$</th><th class="text-nowrap">Asignar US$</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="8" class="text-center text-muted">No hay partidas pendientes con estos filtros. Verifique las aplicaciones y partidas complementarias de los períodos seleccionados.</td></tr>'}</tbody></table>
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
