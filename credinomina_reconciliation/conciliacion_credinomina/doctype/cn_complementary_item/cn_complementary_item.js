async function cn_receivable_recovery_dialog(frm) {
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde antes de documentar la diferencia."));
    return credinomina.document_difference({doctype: frm.doc.doctype, name: frm.doc.name, on_saved: () => frm.reload_doc()});
}

async function cn_render_complementary_balance(frm) {
    const wrapper = frm.fields_dict?.financial_overview?.$wrapper;
    if (!wrapper || frm.is_new()) return;
    const name = frm.doc.name;
    const sequence = frm.cn_balance_sequence = (frm.cn_balance_sequence || 0) + 1;
    // Frappe also uses the list indicator in the form header. Keep its
    // presentation-only result in __onload, never in a persisted amount/status.
    frm.doc.__onload ||= {};
    delete frm.doc.__onload.cn_balance;
    delete frm.doc.__onload.cn_balance_error;
    frm.toolbar?.set_indicator?.();
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.html(`<p class="text-muted">${__("Consultando distribución y saldo…")}</p>`);
    try {
        const {message: value} = await frappe.call({method: "credinomina_reconciliation.complementary_balances.get_balance", args: {item_name: name}});
        if (frm.doc.name !== name || sequence !== frm.cn_balance_sequence) return;
        frm.doc.__onload.cn_balance = {...value, state: value.reconciliation_state || value.financial_status};
        frm.toolbar?.set_indicator?.();
        const cells = [["Importe original US$", value.original_usd], [value.used_label + " US$", value.used_usd], ["Pendiente de conciliar US$", value.pending_usd]];
        const receivableSum = field => value.receivable_companies.some(row => row[field] == null) ? "N/D"
            : format_currency(value.receivable_companies.reduce((sum, row) => sum + Number(row[field]), 0), "USD", 2);
        wrapper.html(`<div class="row">${cells.map(([label, amount]) => `<div class="col-sm-4"><div class="text-muted">${esc(__(label))}</div><strong>${esc(format_currency(amount, "USD", 2))}</strong></div>`).join("")}</div>
            <div class="row mt-3">${[["Conciliación", value.reconciliation_state || value.financial_status], ["Registro contable", value.accounting_status],
                ["Gestión", value.management_pending_usd == null ? "No aplica" : `${value.management_status} · ${format_currency(value.management_pending_usd, "USD", 2)} por gestionar`]]
                .map(([label, status]) => `<div class="col-sm-4"><div class="text-muted">${esc(__(label))}</div><strong>${esc(__(status))}</strong></div>`).join("")}</div>
            ${value.management_pending_usd == null ? "" : `<p class="text-muted mt-2">${__("La distribución del depósito puede estar conciliada y la devolución o aplicación del saldo a favor seguir pendiente.")}</p>`}
            ${value.receivable_companies?.length ? `<div class="alert ${value.receivable_issue_count ? "alert-danger" : (value.receivable_usd ?? value.company_receivable_usd) ? "alert-warning" : "alert-success"}"><strong>${esc(__(value.receivable_issue_count ? "CxC con cobros por revisar" : (value.receivable_usd ?? value.company_receivable_usd) ? "CxC pendiente de cobro" : "CxC cobrada"))}</strong>
                <div class="row mt-2">${[["CxC original US$", "receivable_original_usd"], ["Cobrado US$", "receivable_paid_usd"], ["Compensado US$", "receivable_compensated_usd"], ["Pendiente US$", "receivable_usd"]].map(([label, field]) => `<div class="col-sm-3"><div>${esc(__(label))}</div><strong>${esc(receivableSum(field))}</strong></div>`).join("")}</div>
                ${value.receivable_companies.filter(row => row.observation).map(row => `<p>${esc(row.observation)}</p>`).join("")}
                <div class="mt-2">${__("Registrar el asiento en el core no liquida la deuda. Use Documentar seguimiento de CxC para vincular su liquidación.")}</div></div>` : ""}
            ${value.recoveries?.length ? `<details><summary>${esc(__("Cobros y compensaciones vinculados"))}</summary><table class="table table-bordered"><thead><tr><th>${esc(__("Partida"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Fecha"))}</th><th>${esc(__("Estado"))}</th><th>${esc(__("Original US$"))}</th><th>${esc(__("Liquidado US$"))}</th></tr></thead><tbody>${value.recoveries.map(row => `<tr><td><a href="/app/cn-complementary-item/${encodeURIComponent(row.name)}">${esc(row.name)}</a></td><td>${esc(row.employer)}</td><td>${esc(frappe.datetime.str_to_user(row.posting_date))}</td><td>${esc(__(row.financial_status))}</td><td>${esc(format_currency(row.amount_usd, "USD", 2))}</td><td>${esc(format_currency(row.effective_usd, "USD", 2))}</td></tr>`).join("")}</tbody></table></details>` : ""}
            <p class="text-muted">${__("Tener un asiento informado no significa que la partida esté conciliada. Solo se suman distribuciones realizadas, no destinos seleccionados.")}</p>
            ${value.distributions?.length ? `<details><summary>${__("Depósitos que utilizan esta partida")}</summary><table class="table table-bordered"><thead><tr><th>${__("Depósito")}</th><th>${__("Empresa / cliente")}</th><th>${__("US$")}</th></tr></thead><tbody>${value.distributions.map(row => `<tr><td><a href="/app/cn-remittance-allocation/${encodeURIComponent(row.deposit)}">${esc(row.deposit)}</a></td><td>${esc([row.employer, row.client].filter(Boolean).join(" · "))}</td><td>${esc(format_currency(row.amount_usd, "USD", 2))}</td></tr>`).join("")}</tbody></table></details>` : ""}`);
    } catch (_) {
        if (frm.doc.name === name && sequence === frm.cn_balance_sequence) {
            frm.doc.__onload.cn_balance_error = true;
            frm.toolbar?.set_indicator?.();
            wrapper.html(`<p class="text-danger">${__("No se pudo consultar el saldo. Recargue el formulario; no lo interprete como saldo cero.")}</p>`);
        }
    }
}

function cn_add_cancel_action(frm) {
    frm.remove_custom_button?.(__("Cancelar partida"));
    if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.origin_deposit || frm.doc.receivable_origin
            || ["Diferencia por tolerancia", "Compensación entre partidas", "Vinculado a depósito", "Cobro de CxC"].includes(frm.doc.category)) return;
    // A configured workflow retains ownership of its transitions.
    if (frm.toolbar?.has_workflow?.()) return;
    if (!frm.get_perm(0, "cancel") || !frm.get_perm(0, "write")) {
        if (frm.get_perm(0, "write")) frm.dashboard?.add_comment?.(
            __("Para cancelar esta partida necesita permiso de cancelación. Solicite la revisión de un Supervisor Credinomina."), "blue", true);
        return;
    }
    frm.add_custom_button(__("Cancelar partida"), () => cn_cancel_item(frm));
}

function cn_cancel_item(frm) {
    if (frm._cn_cancelling || frm.is_new() || frm.doc.docstatus !== 1) return;
    if (!frm.get_perm(0, "cancel") || !frm.get_perm(0, "write")) {
        return frappe.msgprint(__("No tiene permiso para cancelar esta partida."));
    }
    if (frm.is_dirty()) return frappe.msgprint(__("Guarde o recargue la partida antes de cancelarla."));
    const name = frm.doc.name, modified = frm.doc.modified;
    frm._cn_cancelling = true;
    return frappe.confirm(
        __("¿Cancelar esta partida? Se conservará el registro cancelado y se recalcularán sus vínculos afectados. Esta acción no cancela los depósitos relacionados."),
        async () => {
            try {
                if (frm.doc.name !== name || frm.doc.modified !== modified || frm.is_dirty()) {
                    return frappe.msgprint(__("La partida cambió. Recargue antes de cancelar."));
                }
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item.cancel_item",
                    args: {item_name: name, modified}, freeze: true, freeze_message: __("Cancelando partida…"),
                });
                if (frm.doc.name === name) await frm.reload_doc();
                frappe.show_alert({message: __("Partida cancelada."), indicator: "green"});
            } finally {frm._cn_cancelling = false;}
        },
        () => {frm._cn_cancelling = false;},
    );
}

function cn_unconfirm_item(frm) {
    const name = frm.doc.name;
    const modified = frm.doc.modified;
    const dialog = new frappe.ui.Dialog({
        title: __("Desconfirmar partida"),
        fields: [
            {fieldtype: "HTML", options: `<p>${__("Se revertirá la conciliación de esta partida y volverá a borrador con el mismo número. Los depósitos y períodos afectados pueden quedar pendientes de revisión.")}</p>`},
            {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo"), reqd: 1},
        ],
        primary_action_label: __("Desconfirmar"),
        async primary_action(values) {
            if (frm.doc.name !== name || frm.is_dirty()) {
                frappe.msgprint(__("Guarde o recargue la partida antes de desconfirmarla."));
                return;
            }
            dialog.disable_primary_action();
            try {
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item.unconfirm_item",
                    args: {item_name: name, modified, reason: values.reason},
                    freeze: true, freeze_message: __("Revirtiendo la confirmación y actualizando saldos…"),
                });
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __("Partida desconfirmada. Revise sus vínculos antes de confirmarla nuevamente."), indicator: "orange"});
            } finally {
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();
}

function cn_can_link_deposit(frm) {
    const doc = frm.doc;
    return !frm.is_new() && [0, 1].includes(doc.docstatus) && !!frm.get_perm(0, "write")
        && (doc.docstatus === 1 || !!frm.get_perm(0, "submit")) && !!doc.employer
        && ["Por clasificar", "Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"].includes(doc.category)
        && !doc.origin_deposit && !doc.registered_deposit && !doc.registration_exception && !doc.receivable_origin
        && !doc.related_application && !(doc.compensations || []).length && !doc.generic_distribution
        && !(doc.docstatus === 1 && doc.review_action === "No conciliatoria")
        && !["Aplicación de pago", "ND de Aplicación de pago"].includes(doc.accounting_classification);
}

async function cn_link_deposit(frm) {
    if (!cn_can_link_deposit(frm)) return;
    if (frm.is_dirty()) await frm.save();
    const itemName = frm.doc.name, draft = frm.doc.docstatus === 0;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    let dialog, context = null, request = 0, busy = false;
    const load = async () => {
        const deposit = dialog.get_value("deposit");
        const id = ++request;
        context = null;
        dialog.disable_primary_action();
        dialog.fields_dict.preview.$wrapper.empty();
        dialog.set_df_property("detail_row", "options", [{value: "", label: __("Sin vincular a una fila del detalle")}]);
        await dialog.set_value("detail_row", "");
        if (!deposit) return;
        dialog.fields_dict.preview.$wrapper.html(`<p>${esc(__("Consultando depósito e importe disponible…"))}</p>`);
        try {
            const response = await frappe.call({method: "credinomina_reconciliation.complementary_deposit_link.get_context",
                args: {item_name: itemName, deposit_name: deposit}});
            if (id !== request || frm.doc.name !== itemName || dialog.get_value("deposit") !== deposit) return;
            context = response.message;
            dialog.fields_dict.preview.$wrapper.html(`<div class="alert alert-info">
                <div>${esc(__("Depósito"))}: <a href="/app/cn-remittance-allocation/${encodeURIComponent(deposit)}" target="_blank" rel="noopener noreferrer">${esc(deposit)}</a> · ${esc(context.date)} · ${esc(context.employer)}</div>
                <div>${esc(__("Referencia"))}: ${esc(context.reference)}</div>
                <div>${esc(__("Importe del depósito"))}: ${esc(format_currency(context.deposit_usd, "USD", 2))}</div>
                <div>${esc(__("Disponible de la partida"))}: ${esc(format_currency(context.available_usd, "USD", 2))}</div>
                <div>${esc(__("Disponible del depósito"))}: ${esc(format_currency(context.cash_usd, "USD", 2))}</div>
                ${context.existing ? `<p>${esc(__("Esta partida ya está vinculada. Abra el depósito para revisar o modificar el destino existente."))}</p>` : ""}</div>`);
            dialog.set_df_property("detail_row", "options", [{value: "", label: __("Sin vincular a una fila del detalle")},
                ...context.rows.map(row => ({value: row.name, label: `${row.idx}. ${row.client_name || ""} · ${row.loan_number || ""} · ${format_currency(row.amount_usd, "USD", 2)}`}))]);
            await dialog.set_value("amount_usd", context.suggested_usd || null);
            if (!context.existing && context.available_usd > 0) dialog.enable_primary_action();
        } catch (error) {
            if (id !== request) return;
            context = null;
            dialog.fields_dict.preview.$wrapper.html(`<p class="text-danger">${esc(__("No se pudo preparar el vínculo. Revise el mensaje y seleccione otro depósito o vuelva a intentar."))}</p>`);
        }
    };
    dialog = new frappe.ui.Dialog({title: __("Vincular a depósito"), size: "large", fields: [
        {fieldtype: "HTML", options: `<p>${esc(__("Seleccione el depósito y revise el importe con su signo. El vínculo se guarda en Destinos; use Conciliar en el depósito para aplicar y validar la distribución."))}</p>`},
        {fieldname: "deposit", fieldtype: "Link", options: "CN Remittance Allocation", label: __("Depósito"), reqd: 1,
            get_query: () => ({query: "credinomina_reconciliation.complementary_deposit_link.deposit_query", filters: {item_name: itemName}}), onchange: load},
        {fieldname: "preview", fieldtype: "HTML"},
        {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Importe a vincular US$ (+ / −)"), reqd: 1,
            description: __("Puede vincular un importe parcial. Positivo consume depósito; negativo documenta un faltante. La partida confirmada conserva su signo.")},
        {fieldname: "detail_row", fieldtype: "Select", label: __("Fila del detalle (opcional)"),
            options: [{value: "", label: __("Sin vincular a una fila del detalle")}],
            description: __("Seleccione la fila del cliente cuando la partida deba explicar ese pago.")},
        ...(draft ? [
            {fieldtype: "Section Break", label: __("Confirmación de la partida")},
            {fieldname: "category", fieldtype: "Select", label: __("Concepto"), reqd: 1,
                options: "Cobranza administrativa\nOtros ingresos\nAjuste de conciliación",
                default: frm.doc.category === "Por clasificar" ? "Ajuste de conciliación" : frm.doc.category},
            {fieldname: "subcategory", fieldtype: "Link", options: "CN Complementary Subcategory", label: __("Subcategoría"),
                default: frm.doc.subcategory || "", depends_on: "eval:doc.category === 'Ajuste de conciliación'",
                mandatory_depends_on: "eval:doc.category === 'Ajuste de conciliación'"},
            {fieldname: "reviewed", fieldtype: "Check", label: __("Revisé el importe y su signo y deseo confirmar esta partida"), reqd: 1},
        ] : []),
    ], primary_action_label: __(draft ? "Confirmar y vincular" : "Vincular"), async primary_action(values) {
        if (busy || !context || context.existing) return;
        if (frm.doc.name !== itemName || frm.is_dirty()) return frappe.msgprint(__("La partida cambió. Guarde o recargue y vuelva a vincular."));
        busy = true;
        dialog.disable_primary_action();
        try {
            const response = await frappe.call({method: "credinomina_reconciliation.complementary_deposit_link.link",
                args: {item_name: itemName, deposit_name: values.deposit,
                    item_modified: context.item_modified, deposit_modified: context.deposit_modified,
                    amount_usd: values.amount_usd, detail_row: values.detail_row || "", category: values.category || "",
                    subcategory: values.subcategory || "", reviewed: Number(values.reviewed || 0)},
                freeze: true, freeze_message: __("Vinculando partida al depósito…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.msgprint({title: __("Partida vinculada"), message: `${esc(__("La partida se agregó a Destinos. Abra el depósito y use Conciliar para validar la distribución."))}<br>
                <a href="/app/cn-remittance-allocation/${encodeURIComponent(response.message.deposit)}">${esc(response.message.deposit)}</a>`});
        } finally { busy = false; dialog.enable_primary_action(); }
    }});
    dialog.show();
    dialog.disable_primary_action();
}

function cn_classify_pending(frm) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const choices = [];
    const add = (label, hint, action) => choices.push({label, hint, action});
    const draft = frm.doc.docstatus === 0 && !frm.doc.receivable_origin && !(frm.doc.compensations || []).length;
    if (draft) {
        add("Identificar empresa o cliente", "Complete los vínculos sin modificar la evidencia contable original.", () => frm.scroll_to_field("employer"));
    }
    for (const [label, hint, action] of [
        ["Vincular a aplicación", "Ajuste o reversión total/parcial del core. Seleccione la aplicación real.", () => cn_select_original_application(frm)],
        ["Vincular a depósito", "Seleccione el depósito y el importe. La partida se agrega directamente a sus destinos.", () => cn_link_deposit(frm)],
        ["Compensar con otra partida", "Vincule la contrapartida; no basta con cambiar el concepto.", () => cn_compensate_items_dialog(frm)],
        ["Documentar seguimiento de CxC", "Documente el soporte; el saldo y los vínculos históricos se conservan.", () => cn_receivable_recovery_dialog(frm)],
        ["Crear excepción", "Documente información faltante o registro pendiente en el core con fecha compromiso.", () => cn_comp_create_exception(frm)],
        ["Ver excepción", "Consulte la gestión y evidencia vinculadas.", () => frappe.set_route("Form", "CN Reconciliation Exception", frm.doc.accounting_exception || frm.doc.registration_exception)],
        ["Abrir depósito", "Revise el detalle y documente allí el saldo a favor del cliente o empresa.", () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.registered_deposit)],
    ]) {
        if (frm.custom_buttons?.[__(label)]) add(label, hint, action);
    }
    if (frm.doc.docstatus === 1 && ["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)
            && Number(frm.doc.credit_pending_usd) > 0 && frm.get_perm(0, "submit")) {
        add("Documentar devolución o aplicación", "Registre la gestión real del saldo a favor. Conciliar no acredita una devolución.", () => cn_record_client_credit_management(frm));
    }
    if (draft && frm.doc.accounting_source_key && !frm.doc.registration_exception
            && !["Ajuste de aplicación", "Compensación entre partidas", "Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)) {
        add("Marcar fuera del flujo de depósitos", "Se guarda como No conciliatoria con explicación. Su saldo permanece por aclarar; no se condona ni se cuenta como conciliado.", async values => {
            if (!values.notes?.trim()) {frappe.msgprint(__("Escriba el motivo de la clasificación.")); return false;}
            await frm.set_value({review_action: "No conciliatoria", review_notes: values.notes.trim()});
            await frm.save();
        });
    }
    if (!choices.length) {frappe.msgprint(__("No hay acciones de clasificación disponibles para este documento.")); return;}
    const preferred = choices.some(item => item.label === "Documentar devolución o aplicación")
        ? "Documentar devolución o aplicación"
        : choices.some(item => item.label === "Documentar seguimiento de CxC")
            ? "Documentar seguimiento de CxC" : choices[0].label;
    let dialog, balance, balanceError = false;
    const documentName = frm.doc.name;
    const describe = () => {
        if (!dialog) return;
        const choice = choices.find(item => item.label === dialog.get_value("action"));
        dialog.fields_dict.help.$wrapper.html(`<div class="alert alert-info">${esc(__(choice?.hint || ""))}<hr>
            ${balance ? `<div class="row">${[["Importe original US$", balance.original_usd], ["Distribuido / compensado US$", balance.used_usd], ["Pendiente financiero US$", balance.pending_usd]]
                .map(([label, value]) => `<div class="col-sm-4"><div>${esc(__(label))}</div><strong>${value == null ? "—" : esc(format_currency(value, "USD", 2))}</strong></div>`).join("")}</div>
                ${balance.management_pending_usd == null ? "" : `<p>${esc(__("Saldo a favor por gestionar"))}: ${esc(format_currency(balance.management_pending_usd, "USD", 2))}</p>`}
                ${(balance.receivable_usd ?? balance.company_receivable_usd) ? `<p>${esc(__("CxC pendiente de cobro"))}: ${esc(format_currency((balance.receivable_usd ?? balance.company_receivable_usd), "USD", 2))}</p>` : ""}
                ${balance.receivable_issue_count ? `<p class="text-danger">${esc(__("CxC con cobros por revisar · importe sin determinar"))}</p>` : ""}
                ${balance.amount_issue ? `<p class="text-danger">${esc(__(balance.amount_issue))}</p>` : ""}` :
                `<p>${esc(__(balanceError ? "No se pudo consultar el saldo; no se interpreta como cero." : "Los saldos se consultan sobre el documento guardado."))}</p>`}
            ${frm.is_dirty?.() ? `<p>${esc(__("Hay cambios sin guardar; esta vista muestra los saldos guardados, no los cambios del formulario."))}</p>` : ""}
            ${esc(__("Clasificar no cobra, devuelve ni compensa dinero. El saldo solo cambia al confirmar un vínculo financiero real."))}
            <p>${esc(__("El destino y el importe se eligen en el siguiente paso. Una clasificación descriptiva no modifica estos saldos."))}</p>
            ${frm.doc.accounting_source_key ? `<br>${esc(__("Se conserva la magnitud contable original; cambiar el signo no permite reducir el importe."))}` : ""}</div>`);
        const notes = choice?.label === "Marcar fuera del flujo de depósitos";
        dialog.set_df_property("notes", "hidden", !notes);
        dialog.set_df_property("notes", "reqd", notes);
    };
    dialog = new frappe.ui.Dialog({title: __("Clasificar pendiente"), fields: [
        {fieldname: "action", fieldtype: "Select", label: __("¿Qué necesita resolver?"), reqd: 1, options: choices.map(item => item.label), default: preferred, onchange: describe},
        {fieldname: "help", fieldtype: "HTML"},
        {fieldname: "notes", fieldtype: "Small Text", label: __("Motivo"), hidden: 1, default: frm.doc.review_notes || ""},
    ], primary_action_label: __("Continuar"), async primary_action(values) {
        const choice = choices.find(item => item.label === values.action);
        if (!choice) return;
        // Close before launching another dialog; failed validation keeps this one available.
        dialog.hide();
        try {if (await choice.action(values) === false) dialog.show();}
        catch (error) {dialog.show(); throw error;}
    }});
    dialog.show();
    describe();
    if (documentName && !frm.doc.__islocal) {
        Promise.resolve(frappe.call({method: "credinomina_reconciliation.complementary_balances.get_balance", args: {item_name: documentName}}))
            .then(response => {
                if (frm.doc.name !== documentName) return;
                balance = response.message;
                describe();
            }).catch(() => {if (frm.doc.name === documentName) {balanceError = true; describe();}});
    }
}

function cn_complementary_amount_help(frm) {
    const category = frm.doc.category;
    let help = "Seleccione el concepto y el tratamiento para conocer el efecto del importe. Clasificar una partida no liquida su saldo.";
    if (["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(category)) {
        help = "Importe positivo del saldo a favor documentado. Su devolución o aplicación se registra en Gestión; no vuelve a distribuir el depósito.";
    } else if (category === "Ajuste de aplicación") {
        help = "Conserve el importe de la partida. Importe del ajuste US$ indica cuánto reduce la aplicación seleccionada y debe ser positivo; el vínculo confirmado determina el efecto. Si proviene del core, debe registrar crédito positivo y débito cero.";
    } else if (category === "Compensación entre partidas") {
        help = "Conserve el importe de origen. Compensar con otra partida permite seleccionar la contrapartida y el importe a cancelar; cambiar el signo no ejecuta la compensación.";
    } else if (category === "Diferencia por tolerancia") {
        help = "Ajuste interno automático: positivo cuando sobra depósito y negativo cuando falta. Su vigencia corresponde a la conciliación que lo generó.";
    } else if (["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Cobro de CxC"].includes(category)
            || frm.doc.review_action === "Partida de depósito") {
        help = "Positivo: absorbe dinero del depósito adicional a las aplicaciones. Negativo: cubre una diferencia cuando el depósito es menor; requiere destinos manuales. Para Ajuste de conciliación, la subcategoría determina la gestión posterior.";
    }
    if (frm.doc.accounting_source_key) help += " Conserve la magnitud de los débitos/créditos importados; una distribución parcial mantiene el remanente.";
    frm.set_df_property("amount", "description", __(help));
}

frappe.ui.form.on("CN Complementary Item", {
    async subcategory(frm) {
        const name = frm.doc.subcategory;
        const result = name ? await frappe.db.get_value("CN Complementary Subcategory", name, "effect") : null;
        if (frm.doc.subcategory !== name) return;
        await frm.set_value("subcategory_effect", result?.message?.effect || "");
        if (["CxC a la empresa", "CxC al Empleado"].includes(frm.doc.subcategory_effect)) {
            if (frm.doc.subcategory_effect === "CxC al Empleado") await frm.set_value({receivable_due_date: null, receivable_due_origin: ""});
            if (!frm.doc.credit_assigned_to) await frm.set_value("credit_assigned_to", frappe.session?.user);
            await frm.trigger("period");
        }
    },
    async period(frm) {
        if (frm.doc.docstatus !== 0 || !frm.doc.period || frm.doc.subcategory_effect !== "CxC a la empresa") return;
        const period = frm.doc.period;
        const r = await frappe.call({method: "credinomina_reconciliation.receivable_terms.preview_deadline", args: {period}});
        if (frm.doc.period === period && frm.doc.subcategory_effect === "CxC a la empresa" && r.message?.due_date) await frm.set_value({receivable_due_date: r.message.due_date, receivable_due_origin: r.message.origin});
    },
    setup(frm) {
        frm.set_query("registered_deposit", () => ({filters: {docstatus: 1,
            ...(frm.doc.employer && frm.doc.category !== "Saldo a favor del cliente" ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("credit_client", () => frm.doc.registered_deposit ? {
            query: "credinomina_reconciliation.client_credit.search_beneficiaries",
            filters: {registered_deposit: frm.doc.registered_deposit, employer: frm.doc.employer || ""},
        } : {filters: frm.doc.employer ? {employer: frm.doc.employer} : {}});
        frm.set_query("credit_assigned_to", () => ({filters: {enabled: 1}}));
        frm.set_query("period", () => ({filters: {status: ["!=", "Cerrado"],
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("employer", "distribution_companies", () => ({filters: {name: ["!=", frm.doc.employer || ""]}}));
    },
    category(frm) {
        cn_complementary_amount_help(frm);
        if (frm.doc.docstatus === 0 && frm.doc.review_action === "Partida de depósito" &&
            ["Ajuste de aplicación", "Compensación entre partidas"].includes(frm.doc.category) &&
            !(frm.doc.compensations || []).length) {
            frm.trigger("review_action");
            return;
        }
        if (frm.doc.category === "Compensación entre partidas" && frm.doc.review_action !== frm.doc.category) {
            frm.set_value("review_action", frm.doc.category);
        }
        if (frm.doc.category === "Ajuste de aplicación" && frm.doc.review_action !== "Ajuste de aplicación") {
            frm.set_value("review_action", "Ajuste de aplicación");
        }
        const credit = frm.doc.category === "Saldo a favor de la empresa";
        const clientCredit = frm.doc.category === "Saldo a favor del cliente";
        if (clientCredit && frm.doc.docstatus === 0) {
            frm.set_value("review_action", "Saldo a favor del cliente");
            if (!frm.doc.credit_assigned_to) frm.set_value("credit_assigned_to", frappe.session?.user);
        } else if (!clientCredit && frm.doc.docstatus === 0 && frm.doc.review_action === "Saldo a favor del cliente") {
            frm.set_value("review_action", "Partida de depósito");
        }
        // Keep invalid prefilled identities visible so the user can clear them.
        ["client_number", "loan_number", "installment_number"].forEach(f => frm.toggle_display(f, (!credit && !frm.doc.generic_distribution) || !!frm.doc[f]));
        frm.set_df_property("reference", "read_only", credit || clientCredit);
    },
    generic_distribution(frm) {
        frm.trigger("category");
        if (frm.doc.generic_distribution) frappe.show_alert({message: __("Distribuya manualmente el importe entre los destinos. La partida comparte un solo saldo entre todas las empresas y clientes autorizados."), indicator: "blue"});
    },
    async review_action(frm) {
        cn_complementary_amount_help(frm);
        if (frm.doc.review_action === "Partida de depósito" && frm.doc.docstatus === 0 &&
            !(frm.doc.compensations || []).length &&
            !["Aplicación de pago", "ND de Aplicación de pago"].includes(frm.doc.accounting_classification)) {
            const hadLink = !!(frm.doc.related_application || frm.doc.related_import);
            const values = {related_application: "", related_import: "", application_adjustment_usd: 0,
                adjustment_periods: "[]", adjustment_collection_rows: "[]", review_status: ""};
            if (["Ajuste de aplicación", "Compensación entre partidas"].includes(frm.doc.category)) {
                values.category = "Ajuste de conciliación";
            }
            await frm.set_value(values);
            frm.remove_custom_button?.(__("Confirmar ajuste"));
            frm.remove_custom_button?.(__("Compensar con otra partida"));
            if (hadLink) frappe.show_alert({message: __("Se retiró el vínculo provisional con la aplicación. La evidencia contable se conserva. Guarde la partida para registrar el cambio."), indicator: "blue"});
            return;
        }
        if (frm.doc.review_action === "Ajuste de aplicación") frm.set_value("category", "Ajuste de aplicación");
        if (frm.doc.review_action === "Compensación entre partidas") frm.set_value("category", "Compensación entre partidas");
        if (frm.doc.review_action === "Saldo a favor del cliente" && frm.doc.category !== frm.doc.review_action) frm.set_value("category", frm.doc.review_action);
    },
    select_credit_detail: cn_select_client_credit_detail,
    credit_detail_row: cn_render_credit_detail,
    record_credit_management: cn_record_client_credit_management,
    async credit_client(frm) {
        if (!frm.doc.credit_client || frm.doc.docstatus !== 0) return;
        const client = frm.doc.credit_client;
        const r = await frappe.db.get_value("CN Client", client, ["client_name", "client_number", "employer"]);
        if (frm.doc.credit_client !== client) return;
        await frm.set_value({client_name: r.message.client_name, client_number: r.message.client_number,
            ...(!frm.doc.registered_deposit && !frm.doc.employer ? {employer: r.message.employer} : {})});
    },
    async registered_deposit(frm) {
        cn_render_credit_detail(frm);
        if (!frm.doc.registered_deposit || !["Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)) return;
        const name = frm.doc.registered_deposit;
        const r = await frappe.db.get_value("CN Remittance Allocation", name,
            ["employer", "deposit_reference", "deposit_voucher", "deposit_date"]);
        if (frm.doc.registered_deposit !== name || !["Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)) return;
        await frm.set_value({...(frm.doc.category === "Saldo a favor de la empresa" ? {employer: r.message.employer} : {}), reference: r.message.deposit_reference,
            deposit_voucher: r.message.deposit_voucher, posting_date: frm.doc.posting_date || r.message.deposit_date});
    },
    refresh(frm) {
        cn_add_cancel_action(frm);
        window.credinominaWorkbenchHandoff?.(frm, result => frm.doc.docstatus === 1
            ? cn_distribute_applications(frm, result) : cn_select_original_application(frm, result));
        if (frm.doc.origin_deposit) {
            frm.toolbar?.set_indicator?.();
            frm.dashboard.set_headline_alert(__("Vinculado a depósito. Se conserva como evidencia, sin saldo disponible y sin contar como partida conciliada ni pendiente. No puede utilizarse en otro depósito ni compensar otras partidas."), "blue");
            frm.add_custom_button(__("Abrir depósito vinculado"), () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.origin_deposit));
            frm.disable_save();
            return;
        }
        frm.set_df_property("receivable_due_date", "read_only", frm.doc.docstatus === 1 && !!frm.doc.receivable_due_date);
        if (frm.doc.receivable_setup_pending) frm.dashboard.set_headline_alert(
            __("CxC con seguimiento incompleto: verifique responsable, compromiso de gestión y vencimiento financiero. No reprograma ni liquida la deuda."), "orange");
        cn_complementary_amount_help(frm);
        cn_render_complementary_balance(frm);
        cn_render_credit_detail(frm);
        cn_comp_add_exception_button(frm);
        if (frm.doc.docstatus === 1 && ["CxC a la empresa", "CxC al Empleado"].includes(frm.doc.subcategory_effect) && frm.get_perm(0, "write") && frm.get_perm(0, "submit")) {
            frm.add_custom_button(__("Documentar seguimiento de CxC"), () => cn_receivable_recovery_dialog(frm));
        }
        const automatic = frm.doc.category === "Diferencia por tolerancia";
        const categories = ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Saldo a favor de la empresa", "Saldo a favor del cliente", "Ajuste de aplicación", "Compensación entre partidas"];
        if (frm.doc.accounting_source_key) categories.unshift("Por clasificar");
        if (automatic) categories.push("Diferencia por tolerancia");
        if (frm.doc.receivable_origin) categories.push("Cobro de CxC");
        frm.set_df_property("category", "options", categories.join("\n"));
        if (automatic) {
            if (!frm._cn_tolerance_read_only) frm._cn_tolerance_read_only = new Map(
                frm.fields.map(field => [field.df.fieldname, field.df.read_only || 0]));
            frm.fields.forEach(field => frm.set_df_property(field.df.fieldname, "read_only", 1));
            frm.disable_save();
            frm.dashboard.set_headline_alert(frm.doc.accounting_exception
                ? __("Ajuste automático por tolerancia. El registro en el core se gestiona en su excepción; su vigencia financiera se controla al conciliar.")
                : __("Tolerancia histórica conservada para revisión. No se generan ni revierten partidas por tolerancia automáticamente."), "blue");
            return;
        }
        if (frm._cn_tolerance_read_only) {
            for (const [fieldname, readOnly] of frm._cn_tolerance_read_only) frm.set_df_property(fieldname, "read_only", readOnly);
            delete frm._cn_tolerance_read_only;
            if (frm.get_perm(0, "write")) frm.enable_save();
        }
        frm.trigger("category");
        if (frm.doc.receivable_origin) {
            for (const field of ["category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "reference", "related_application", "related_import", "registered_deposit", "period"]) frm.set_df_property(field, "read_only", 1);
        }
        cn_client_credit_history(frm);
        if (frm.doc.registered_deposit) frm.add_custom_button(__("Abrir depósito"),
            () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.registered_deposit));
        if (frm.doc.docstatus === 2) return;
        if (frm.doc.docstatus === 1 && frm.get_perm(0, "cancel") && frm.get_perm(0, "write")
                && !frm.doc.receivable_origin && !["Diferencia por tolerancia", "Compensación entre partidas", "Vinculado a depósito", "Cobro de CxC"].includes(frm.doc.category)) {
            frm.page.add_menu_item(__("Desconfirmar"), () => cn_unconfirm_item(frm));
        }
        if (!frm.is_new() && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Clasificar pendiente"), () => cn_classify_pending(frm));
        }
        const compensated = (frm.doc.compensations || []).length > 0;
        const betweenItems = frm.doc.category === "Compensación entre partidas";
        if (cn_can_link_deposit(frm)) {
            frm.add_custom_button(__("Vincular a depósito"), () => cn_link_deposit(frm));
        }
        const documentedCredit = ["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)
            && frm.doc.docstatus === 1 && frm.doc.result === "Saldo a favor documentado" && !!frm.doc.registered_deposit;
        if (documentedCredit && compensated && frm.get_perm(0, "submit") && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Revisar tratamiento de compensación"), () => cn_review_compensation_effect(frm));
        }
        for (const field of ["category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "period", "client_number", "loan_number", "description"]) {
            frm.set_df_property(field, "read_only", compensated || frm.doc.receivable_origin ? 1 : 0);
        }
        const editableLoan = !frm.doc.generic_distribution && !frm.doc.registration_exception
            && frm.doc.subcategory_effect !== "CxC al Empleado" && frm.doc.review_action === "Partida de depósito"
            && ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"].includes(frm.doc.category);
        frm.set_df_property("loan_number", "read_only", compensated || frm.doc.receivable_origin
            || frm.doc.docstatus === 1 && !editableLoan ? 1 : 0);
        if (compensated) frm.set_df_property("reference", "read_only", 1);
        if (!frm.is_new() && (betweenItems || compensated)) {
            frm.add_custom_button(__("Consultar saldo a fecha"), () => cn_compensation_balance_dialog(frm));
            if (compensated && frm.get_perm(0, "submit") && frm.get_perm(0, "write")) {
                if (!frm.doc.receivable_origin) frm.add_custom_button(__("Eliminar compensación del historial"), () => cn_delete_complementary_history(frm, "compensation"), __("Más acciones"));
                frm.add_custom_button(__("Revertir compensación"), () => cn_reverse_compensation(frm));
                if (!frm.doc.receivable_origin) frm.add_custom_button(__("Corregir fecha de compensación"), () => cn_correct_compensation_date(frm));
            }
        }
        if ((documentedCredit || ((frm.doc.docstatus === 0 || betweenItems)
            && !["Ajuste de aplicación", "Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)
            && !frm.doc.registered_deposit)) && !frm.is_new() && frm.get_perm(0, "submit") && frm.get_perm(0, "write")
            && !frm.doc.related_application && !frm.doc.registration_exception
            && !frm.doc.credit_usage_issue
            && (documentedCredit ? (frm.doc.credit_pending_usd > 0 || (frm.doc.credit_history && frm.doc.credit_history !== "[]"
                && (!compensated || frm.doc.compensation_pending_usd > 0))) : (!compensated || frm.doc.compensation_pending_usd > 0))) {
            frm.add_custom_button(__("Compensar con otra partida"), () => cn_compensate_items_dialog(frm));
        }
        if (!frm.is_new() && frm.get_perm(0, "write") && !betweenItems && !compensated
                && ((frm.doc.docstatus === 0 && !["Saldo a favor del cliente", "Saldo a favor de la empresa", "Diferencia por tolerancia"].includes(frm.doc.category))
                    || (frm.doc.docstatus === 1 && frm.doc.category === "Ajuste de aplicación" && frm.get_perm(0, "submit")))) {
            frm.add_custom_button(__("Distribuir entre aplicaciones"), () => cn_distribute_applications(frm));
        }
        cn_render_application_links(frm);
        if (frm.doc.docstatus === 0 && !frm.is_new()) {
            if (!betweenItems && !compensated && frm.doc.category !== "Saldo a favor del cliente") frm.add_custom_button(__("Vincular a aplicación"), () => cn_select_original_application(frm));
            if (frm.doc.review_action === "Ajuste de aplicación" && frm.get_perm(0, "submit")) {
                frm.add_custom_button(__("Confirmar ajuste"), () => frappe.confirm(
                    __("Se reducirá el aplicado neto de la aplicación vinculada. No se registrará un depósito. ¿Confirmar ajuste?"),
                    async () => {
                        if (frm.is_dirty()) await frm.save();
                        await frappe.call({method: "credinomina_reconciliation.application_adjustments.confirm_adjustment",
                            args: {item_name: frm.doc.name}, freeze: true, freeze_message: __("Confirmando ajuste y recalculando saldos…")});
                        await frm.reload_doc();
                        frappe.show_alert({message: __("Ajuste confirmado. Los saldos de la empresa fueron actualizados."), indicator: "green"});
                    }));
            }
        }
        if (["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)) {
            const confirmed = frm.doc.docstatus === 1;
            for (const field of ["category", "review_action", "registered_deposit", "credit_client", "credit_detail_row", "employer", "period", "client_number", "loan_number", "amount", "currency", "fx_rate", "posting_date"]) frm.set_df_property(field, "read_only", confirmed ? 1 : 0);
            frm.toggle_display("select_credit_detail", !confirmed && frm.doc.category === "Saldo a favor del cliente");
            frm.toggle_display("record_credit_management", confirmed && Number(frm.doc.credit_pending_usd) > 0 && !!frm.get_perm(0, "submit"));
            if (confirmed && Number(frm.doc.credit_pending_usd) > 0 && frm.get_perm(0, "submit") && frm.get_perm(0, "write")) {
                frm.add_custom_button(__("Aplicar a aplicación contable"), () => cn_apply_credit_to_application(frm));
                frm.add_custom_button(__("Documentar devolución o aplicación"), () => cn_record_client_credit_management(frm));
                frm.change_custom_button_type?.(__("Aplicar a aplicación contable"), null, "primary");
            }
            if (confirmed && frm.get_perm(0, "submit") && frm.get_perm(0, "write") && frm.doc.credit_history && frm.doc.credit_history !== "[]") {
                frm.add_custom_button(__("Eliminar gestión del historial"), () => cn_delete_complementary_history(frm, "management"), __("Más acciones"));
                frm.add_custom_button(__("Revertir gestión"), () => cn_reverse_credit_management(frm), __("Más acciones"));
            }
            frm.dashboard.set_headline_alert(frm.doc.credit_usage_issue
                ? __("Saldo bloqueado para nuevos usos. Revise el tratamiento de las compensaciones y seleccione la gestión que respalda cada vínculo contable.") : confirmed
                ? __("Saldo a favor documentado. Gestión: {0}. Use Aplicar a aplicación contable para cubrir una aplicación importada y conservar el remanente. Documentar devolución o aplicación solo registra una gestión externa. El depósito original conserva su distribución.", [frm.doc.credit_management_status || "Pendiente"])
                : __("Identifique el beneficiario y depósito del excedente. Indique importe positivo, motivo, responsable y fecha compromiso. Si esta partida ya fue importada, reclasifíquela aquí; no cree otra copia."), frm.doc.credit_usage_issue ? "orange" : "blue");
            return;
        }
        if (betweenItems) {
            frm.dashboard.set_headline_alert(__("{0}. Use Compensar con otra partida para confirmar nuevas compensaciones. No registra depósitos ni modifica aplicaciones. El historial conserva ambas partidas.",
                [frm.doc.compensation_status || __("Sin compensar")]), frm.doc.compensation_pending_usd === 0 && compensated ? "green" : "orange");
            return;
        }
        if (frm.doc.accounting_source_key && frm.doc.docstatus === 0) {
            if (frm.doc.review_action === "Partida de depósito") {
                frm.dashboard.set_headline_alert(__("Use Vincular a depósito para seleccionar el depósito, revisar el importe y agregar la partida a sus destinos. Después use Conciliar en el depósito."), "blue");
                return;
            }
            frm.dashboard.set_headline_alert(__("Vincular no afecta saldos. Para reducir una aplicación, revise el importe y use Confirmar ajuste. El movimiento original se conserva."), "orange");
            return;
        }
        if (!frm.doc.voucher) {
            frm.dashboard.set_headline_alert(__("Pendiente de registro contable: complete el Asiento contable cuando se registre, incluso si la partida ya está confirmada."), "orange");
        } else {
            frm.dashboard.clear_headline();
        }
    },
});

function cn_comp_add_exception_button(frm) {
    if (frm.is_new() || frm.doc.docstatus === 2 || frm.doc.receivable_origin) return;
    if (frm.doc.accounting_exception || frm.doc.registration_exception) {
        frm.add_custom_button(__("Ver excepción"), () => frappe.set_route("Form", "CN Reconciliation Exception",
            frm.doc.accounting_exception || frm.doc.registration_exception));
    } else if (frappe.model?.can_create?.("CN Reconciliation Exception")) {
        frm.add_custom_button(__("Crear excepción"), () => cn_comp_create_exception(frm));
    }
}

async function cn_select_client_credit_detail(frm) {
    if (!frm.doc.registered_deposit) {frappe.msgprint(__("Seleccione primero el depósito de origen.")); return;}
    const deposit = frm.doc.registered_deposit;
    const response = await frappe.call({method: "credinomina_reconciliation.client_credit.get_deposit_detail", args: {deposit_name: deposit}});
    if (frm.doc.registered_deposit !== deposit) return;
    const rows = (response.message.rows || []).filter(row => !frm.doc.credit_client || row.client === frm.doc.credit_client || row.client_number === frm.doc.client_number);
    const dialog = new frappe.ui.Dialog({title: __("Seleccionar fila del depósito"), fields: [
        {fieldname: "row", fieldtype: "Select", label: __("Fila que ya incluye el excedente"), default: frm.doc.credit_detail_row || "", options: [{value: "", label: "Excedente fuera del detalle por cliente"}, ...rows.map(row => ({value: row.name,
            label: `${row.client_name || row.client_number} · ${row.loan_number || "Sin crédito"} · ${format_currency(row.amount_usd, "USD")}`}))],
            description: __("Vincule únicamente si el importe de la fila contiene el saldo del cliente. Un excedente informado fuera del detalle no reduce sus pagos.")},
    ], primary_action_label: __("Vincular fila"), async primary_action(values) {
        const row = rows.find(row => row.name === values.row);
        if (!row) {await frm.set_value("credit_detail_row", ""); dialog.hide(); return;}
        await frm.set_value({credit_detail_row: row.name, loan_number: row.loan_number || "",
            ...(row.client ? {credit_client: row.client} : {}), client_number: row.client_number || ""});
        dialog.hide();
    }});
    dialog.show();
}

async function cn_render_credit_detail(frm) {
    const wrapper = frm.fields_dict?.credit_detail_preview?.$wrapper;
    if (!wrapper) return;
    const deposit = frm.doc.registered_deposit, rowId = frm.doc.credit_detail_row;
    const sequence = frm.cn_credit_detail_sequence = (frm.cn_credit_detail_sequence || 0) + 1;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    if (!rowId || !deposit) {
        wrapper.html(`<p class="text-muted">${__("Excedente fuera del detalle por cliente.")} ${frm.doc.category === "Saldo a favor del cliente" ? __("Seleccione una fila solamente si su importe incluye este excedente.") : ""}</p>`);
        return;
    }
    wrapper.html(`<p class="text-muted">${__("Consultando fila de origen…")}</p>`);
    try {
        const {message} = await frappe.call({method: "credinomina_reconciliation.client_credit.get_deposit_detail", args: {deposit_name: deposit}});
        if (sequence !== frm.cn_credit_detail_sequence || deposit !== frm.doc.registered_deposit || rowId !== frm.doc.credit_detail_row) return;
        const row = (message.rows || []).find(item => item.name === rowId);
        wrapper.html(row ? `<p><strong>${__("Fila vinculada del depósito")}</strong><br>${esc(row.client_name || "")} · ${esc(__("Nro. Cliente"))}: ${esc(row.client_number || "—")} · ${esc(__("Crédito"))}: ${esc(row.loan_number || "—")}<br>${esc(__("Importe del detalle"))}: ${esc(format_currency(row.amount_usd, "USD", 2))}</p>`
            : `<p class="text-warning">${__("La fila vinculada ya no está en el detalle. Revise el depósito antes de continuar.")}</p>`);
    } catch (_) {
        if (sequence === frm.cn_credit_detail_sequence) wrapper.html(`<p class="text-warning">${__("No se pudo consultar la fila de origen. El vínculo se conserva; revise el depósito.")}</p>`);
    }
}

function cn_client_credit_history(frm) {
    const wrapper = frm.fields_dict?.credit_history_preview?.$wrapper;
    if (!wrapper) return;
    let history = [];
    try {history = JSON.parse(frm.doc.credit_history || "[]");} catch (_) { /* No unsafe HTML from invalid JSON. */ }
    if (!Array.isArray(history)) history = [];
    const esc = v => frappe.utils.escape_html(String(v ?? ""));
    const proof = url => /^(\/private\/files\/|\/files\/|https?:\/\/)/i.test(url || "")
        ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${__("Ver soporte")}</a>` : "—";
    const destination = row => row.application_id
        ? `${row.import_name ? `<a href="/app/cn-accounting-import/${encodeURIComponent(row.import_name)}">${esc(row.import_name)}</a>` : esc(row.application_id)}<br>${esc(row.application_id)}${row.period ? `<br><a href="/app/cn-reconciliation-period/${encodeURIComponent(row.period)}">${esc(row.period)}</a>` : ""}`
        : "—";
    wrapper.html(history.length ? `<div style="overflow:auto"><table class="table table-bordered"><thead><tr>
        <th>${__("Fecha")}</th><th>${__("Gestión")}</th><th>${__("US$")}</th><th>${__("Referencia")}</th><th>${__("Aplicación / período")}</th><th>${__("Soporte")}</th><th>${__("Registrado por")}</th><th>${__("Observaciones")}</th></tr></thead><tbody>
        ${history.map(row => `<tr><td>${esc(frappe.datetime.str_to_user(row.fecha))}</td><td>${esc(row.tratamiento)}</td><td>${esc(format_currency(row.importe_usd, "USD"))}</td><td>${esc(row.referencia)}</td><td>${destination(row)}</td><td>${proof(row.soporte)}</td><td>${esc(row.usuario)}</td><td>${esc(row.observaciones)}</td></tr>`).join("")}</tbody></table></div>`
        : `<p class="text-muted">${__("Sin gestiones documentadas. Clasificar el saldo no significa que ya fue devuelto o aplicado.")}</p>`);
}

async function cn_delete_complementary_history(frm, kind) {
    if (frm.is_dirty()) return frappe.msgprint(__("Recargue o guarde el formulario antes de eliminar una operación del historial."));
    const itemName = frm.doc.name;
    const method = "credinomina_reconciliation.complementary_history.";
    const response = await frappe.call({method: method + "deletion_options", args: {item_name: itemName, kind}});
    if (frm.doc.name !== itemName) return;
    const rows = response.message?.rows || [];
    if (!rows.length) return frappe.msgprint(__("No hay operaciones para eliminar."));
    const choices = new Map(rows.map((row, index) => [
        `${index + 1}. ${row.description} · ${row.date || __("Sin fecha")} · ${format_currency(row.amount_usd, "USD")} · ${row.count} ${__("movimientos")}`, row.entry_id]));
    let busy = false;
    const selector = new frappe.ui.Dialog({title: __("Eliminar operación del historial"), fields: [
        {fieldtype: "HTML", options: `<p>${__("Se elimina la operación junto con sus reversiones y correcciones relacionadas. Los saldos se recalculan también en los cortes históricos. No se crea una nueva reversión.")}</p>`},
        {fieldname: "operation", fieldtype: "Select", label: __("Operación"), options: [...choices.keys()], reqd: 1},
    ], primary_action_label: __("Revisar eliminación"), async primary_action(values) {
        if (busy || !choices.has(values.operation)) return;
        busy = true;
        selector.get_primary_btn().prop("disabled", true);
        try {
            const entry_id = choices.get(values.operation);
            const preview = (await frappe.call({method: method + "preview_deletion", args: {item_name: itemName, kind, entry_id}})).message;
            if (frm.doc.name !== itemName) return;
            const esc = value => frappe.utils.escape_html(String(value ?? ""));
            const summary = (preview.changes || []).map(row => `<li><strong>${esc(row.name)}</strong>: ${esc(row.removed_count)} ${__("movimientos eliminados")}${row.credit_pending_usd != null ? `; ${__("saldo a favor disponible")}: ${esc(format_currency(row.credit_pending_usd, "USD"))}` : `; ${__("compensado")}: ${esc(format_currency(row.compensated_usd, "USD"))}`}</li>`).join("");
            selector.hide();
            let deleting = false;
            const review = new frappe.ui.Dialog({title: __("Confirmar eliminación del historial"), fields: [
                {fieldtype: "HTML", options: `<ul>${summary}</ul><p>${__("La contrapartida o el período de la aplicación se recalcularán automáticamente. El motivo y los registros retirados quedan en los comentarios de auditoría. Reconstruya los reportes preparados después de la corrección.")}</p>`},
                {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo de la eliminación"), reqd: 1},
            ], primary_action_label: __("Eliminar y recalcular"), async primary_action(values) {
                if (deleting) return;
                deleting = true;
                review.get_primary_btn().prop("disabled", true);
                try {
                    await frappe.call({method: method + "delete_history", args: {item_name: itemName, kind, entry_id, token: preview.token, reason: values.reason},
                        freeze: true, freeze_message: __("Eliminando operación y recalculando saldos…")});
                    review.hide();
                    if (frm.doc.name === itemName) await frm.reload_doc();
                    frappe.show_alert({message: __("Operación eliminada y saldos recalculados."), indicator: "green"});
                } finally {deleting = false; review.get_primary_btn().prop("disabled", false);}
            }});
            review.show();
        } finally {busy = false; selector.get_primary_btn().prop("disabled", false);}
    }});
    selector.show();
}

function cn_management_choices(rows) {
    return new Map(rows.map((row, index) => [
        `${index + 1}. ${row.treatment} · ${row.date || __("Sin fecha")} · ${row.reference || ""} · ${format_currency(row.amount_usd, "USD")}`, row.entry_id]));
}

async function cn_review_compensation_effect(frm) {
    if (frm.is_dirty()) return frappe.msgprint(__("Recargue el formulario antes de revisar el tratamiento."));
    const name = frm.doc.name, method = "credinomina_reconciliation.compensation_effect.";
    const data = (await frappe.call({method: method + "options", args: {item_name: name}})).message;
    if (frm.doc.name !== name) return;
    const operations = new Map((data.operations || []).map((row, index) => [
        `${index + 1}. ${row.counterpart} · ${format_currency(row.amount_usd, "USD")} · ${row.credit_effect}`, row.operation_id]));
    if (!operations.size) return frappe.msgprint(__("No hay compensaciones para revisar."));
    const management = cn_management_choices(data.management || []);
    let busy = false;
    const dialog = new frappe.ui.Dialog({title: __("Tratamiento de la compensación"), fields: [
        {fieldtype: "HTML", options: `<p>${__("Consume saldo reduce la disponibilidad. Vínculo de gestión respalda una devolución o aplicación ya registrada y no vuelve a descontarla. Se conservan ambos historiales y sus fechas.")}</p>`},
        {fieldname: "operation", fieldtype: "Select", label: __("Compensación"), options: [...operations.keys()], reqd: 1},
        {fieldname: "effect", fieldtype: "Select", label: __("Tratamiento"), options: ["Consume saldo", "Vínculo de gestión"], default: "Vínculo de gestión", reqd: 1},
        {fieldname: "management", fieldtype: "Select", label: __("Gestión ya registrada"), options: ["", ...management.keys()], depends_on: "eval:doc.effect == 'Vínculo de gestión'", mandatory_depends_on: "eval:doc.effect == 'Vínculo de gestión'"},
    ], primary_action_label: __("Revisar saldo resultante"), async primary_action(values) {
        if (busy || !operations.has(values.operation)) return;
        busy = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            const args = {item_name: name, operation_id: operations.get(values.operation), credit_effect: values.effect,
                credit_management_entry: values.effect === "Vínculo de gestión" ? management.get(values.management) : ""};
            const result = (await frappe.call({method: method + "preview", args})).message;
            if (frm.doc.name !== name) return;
            dialog.hide();
            let saving = false;
            const review = new frappe.ui.Dialog({title: __("Confirmar tratamiento"), fields: [
                {fieldtype: "HTML", options: `<p>${__("Saldo disponible resultante")}: <strong>${frappe.utils.escape_html(format_currency(result.credit_pending_usd, "USD"))}</strong></p><p>${__("Se actualizará el tratamiento en ambas partidas. No se eliminarán gestiones ni compensaciones.")}</p>`},
                {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo y evidencia del vínculo"), reqd: 1},
            ], primary_action_label: __("Guardar tratamiento"), async primary_action(values) {
                if (saving) return;
                saving = true;
                review.get_primary_btn().prop("disabled", true);
                try {
                    await frappe.call({method: method + "classify", args: {...args, token: result.token, reason: values.reason}, freeze: true});
                    review.hide();
                    if (frm.doc.name === name) await frm.reload_doc();
                    frappe.show_alert({message: __("Tratamiento actualizado. Reconstruya los reportes preparados."), indicator: "green"});
                } finally {saving = false; review.get_primary_btn().prop("disabled", false);}
            }});
            review.show();
        } finally {busy = false; dialog.get_primary_btn().prop("disabled", false);}
    }});
    dialog.show();
}

async function cn_reverse_credit_management(frm) {
    if (frm._cn_credit_management_busy) return;
    if (frm.is_dirty()) await frm.save();
    const itemName = frm.doc.name;
    const response = await frappe.call({method: "credinomina_reconciliation.client_credit.get_management_history", args: {item_name: itemName}});
    if (frm.doc.name !== itemName) return;
    const data = response.message || {};
    const entries = (data.rows || []).filter(row => row.can_reverse);
    if (!entries.length) return frappe.msgprint(__("No hay gestiones pendientes de reversión."));
    const choices = new Map(entries.map((row, index) => [
        `${index + 1}. ${row.tratamiento} · ${format_currency(row.importe_usd, "USD")} · ${row.fecha ? frappe.datetime.str_to_user(row.fecha) : __("Sin fecha")}`, row.entry_id]));
    let busy = false;
    const dialog = new frappe.ui.Dialog({title: __("Revertir gestión documentada"), fields: [
        {fieldtype: "HTML", options: `<p>${__("Conserva el registro original y su soporte. Si revierte una aplicación contable del saldo, se restaura el remanente y el pendiente del período de destino. No revierte pagos en el core ni modifica el depósito original.")}</p>`},
        {fieldname: "management", fieldtype: "Select", label: __("Gestión a revertir"), options: [...choices.keys()], reqd: 1},
        {fieldname: "event_date", fieldtype: "Date", label: __("Fecha de reversión"), default: frappe.datetime.get_today(), reqd: 1},
        {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo de la corrección"), reqd: 1},
    ], primary_action_label: __("Confirmar reversión"), async primary_action(values) {
        if (busy || frm._cn_credit_management_busy || !choices.has(values.management)) return;
        busy = frm._cn_credit_management_busy = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: "credinomina_reconciliation.client_credit.reverse_management",
                args: {item_name: itemName, modified: data.modified, entry_id: choices.get(values.management), event_date: values.event_date, reason: values.reason},
                freeze: true, freeze_message: __("Registrando reversión…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.show_alert({message: __("Reversión registrada. El depósito conserva su distribución original."), indicator: "green"});
        } finally {busy = frm._cn_credit_management_busy = false; dialog.get_primary_btn().prop("disabled", false);}
    }});
    dialog.show();
}

async function cn_record_client_credit_management(frm) {
    if (frm._cn_credit_management_busy) return;
    if (frm.is_dirty()) await frm.save();
    if (frm.doc.docstatus !== 1 || !["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category) || !(Number(frm.doc.credit_pending_usd) > 0)) return;
    let busy = false;
    const dialog = new frappe.ui.Dialog({title: __("Documentar devolución o aplicación"), size: "large", fields: [
        {fieldtype: "HTML", options: `<p>${__("Registre solo gestiones realizadas fuera de esta herramienta, con referencia del core o comprobante. No genera asientos, depósitos ni aplicaciones nuevas.")}</p>`},
        {fieldname: "treatment", fieldtype: "Select", label: __("Gestión realizada"), options: "Devolución\nAplicación futura", reqd: 1,
            default: frm.doc.credit_treatment === "Aplicación futura" ? "Aplicación futura" : "Devolución"},
        {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Importe gestionado US$"), reqd: 1, default: frm.doc.credit_pending_usd},
        {fieldname: "event_date", fieldtype: "Date", label: __("Fecha de gestión"), reqd: 1, default: frappe.datetime.get_today()},
        {fieldtype: "Column Break"},
        {fieldname: "reference", fieldtype: "Data", label: __("Referencia del core / comprobante"), reqd: 1},
        {fieldname: "support_file", fieldtype: "Attach", label: __("Soporte adjunto a la partida (opcional)"),
            options: {doctype: frm.doctype || frm.doc.doctype, docname: frm.doc.name}},
        {fieldname: "notes", fieldtype: "Small Text", label: __("Observaciones")},
    ], primary_action_label: __("Registrar gestión"), async primary_action(values) {
        if (busy || frm._cn_credit_management_busy) return;
        busy = frm._cn_credit_management_busy = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: "credinomina_reconciliation.client_credit.record_management",
                args: {item_name: frm.doc.name, modified: frm.doc.modified, ...values}, freeze: true, freeze_message: __("Registrando seguimiento del saldo…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.show_alert({message: __("Gestión documentada. La distribución original del depósito se conserva."), indicator: "green"});
        } finally {busy = frm._cn_credit_management_busy = false; dialog.get_primary_btn().prop("disabled", false);}
    }});
    dialog.show();
}

async function cn_apply_credit_to_application(frm) {
    if (frm._cn_credit_management_busy) return;
    frm._cn_credit_management_busy = true;
    try {
        if (frm.is_dirty()) await frm.save();
        const itemName = frm.doc.name;
        const {message: data} = await frappe.call({method: "credinomina_reconciliation.credit_application.candidates",
            args: {item_name: itemName}});
        if (frm.doc.name !== itemName) return;
        if (!data?.rows?.length) return frappe.msgprint(__("No hay aplicaciones pendientes del beneficiario en períodos abiertos. Importe y vincule primero la aplicación a su período."));
        const choices = new Map(data.rows.map((row, index) => [
            `${index + 1}. ${row.event_date} · ${row.client_name || ""} · ${row.loan_number || ""} · ${row.reference || ""} · ${row.period} · ${format_currency(row.pending_usd, "USD")}`, row]));
        let busy = false;
        const first = choices.keys().next().value;
        let dialog;
        const update = () => {
            if (!dialog) return;
            const row = choices.get(dialog.get_value("application"));
            if (!row) return;
            dialog.set_value("amount_usd", Math.min(Number(data.pending_usd), Number(row.pending_usd)));
            dialog.set_value("event_date", [row.event_date, frm.doc.posting_date].filter(Boolean).sort().at(-1));
        };
        dialog = new frappe.ui.Dialog({title: __("Aplicar saldo a aplicación contable"), size: "large", fields: [
            {fieldtype: "HTML", options: `<p>${__("Reduce el pendiente de la aplicación importada y el saldo a favor por el importe indicado. Conserva el remanente y la distribución del depósito original, aunque su período esté cerrado.")}</p>`},
            {fieldname: "application", fieldtype: "Select", label: __("Aplicación contable pendiente"), options: [...choices.keys()], default: first, reqd: 1, onchange: update},
            {fieldname: "amount_usd", fieldtype: "Currency", options: "USD", precision: 2, label: __("Importe a aplicar US$"), reqd: 1},
            {fieldname: "event_date", fieldtype: "Date", label: __("Fecha de aplicación del saldo"), reqd: 1},
            {fieldname: "notes", fieldtype: "Small Text", label: __("Observaciones")},
        ], primary_action_label: __("Aplicar saldo"), async primary_action(values) {
            if (busy || frm._cn_credit_management_busy) return;
            const row = choices.get(values.application);
            if (!row) return;
            busy = frm._cn_credit_management_busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                const {message: result} = await frappe.call({method: "credinomina_reconciliation.credit_application.apply",
                    args: {item_name: itemName, modified: data.modified, application_id: row.name,
                        amount_usd: values.amount_usd, event_date: values.event_date, notes: values.notes},
                    freeze: true, freeze_message: __("Aplicando saldo y actualizando el período…")});
                dialog.hide();
                await frm.reload_doc();
                frappe.msgprint(__("Saldo aplicado. Remanente: {0}. Pendiente de la aplicación: {1}.",
                    [format_currency(result.pending_usd, "USD"), format_currency(result.application_pending_usd, "USD")]));
            } finally {
                busy = frm._cn_credit_management_busy = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        }});
        dialog.show();
        update();
    } finally {frm._cn_credit_management_busy = false;}
}

async function cn_comp_create_exception(frm) {
    if (frm._cn_creating_exception) return;
    frm._cn_creating_exception = true;
    const api = "credinomina_reconciliation.complementary_exceptions.";
    try {
        if (frm.is_dirty()) await frm.save();
        const existing = await frappe.call({method: api + "get_item_exception", args: {item_name: frm.doc.name}});
        if (existing.message) {
            frappe.set_route("Form", "CN Reconciliation Exception", existing.message);
            return;
        }
        let busy = false;
        const dialog = new frappe.ui.Dialog({
            title: __("Registrar ajuste en el core"),
            fields: [
                {fieldname: "assigned_to", fieldtype: "Link", options: "User", label: __("Responsable"),
                    reqd: 1, default: frappe.session.user, get_query: () => ({filters: {enabled: 1}})},
                {fieldname: "commitment_date", fieldtype: "Date", label: __("Fecha compromiso"), reqd: 1},
                {fieldname: "next_action", fieldtype: "Small Text", label: __("Próxima gestión"),
                    read_only: 1, default: __("Registrar ajuste en el core")},
                {fieldname: "help", fieldtype: "HTML", options: `<p class="text-muted">${frappe.utils.escape_html(__(
                    "La excepción se resolverá al verificar el asiento en los movimientos contables importados. No cambia la distribución del depósito ni registra asientos en el core."))}</p>`},
            ],
            primary_action_label: __("Crear excepción"),
            primary_action: async values => {
                if (busy) return;
                busy = true;
                dialog.get_primary_btn().prop("disabled", true);
                try {
                    const response = await frappe.call({method: api + "create_item_exception", args: {
                        item_name: frm.doc.name, assigned_to: values.assigned_to, commitment_date: values.commitment_date,
                    }, freeze: true, freeze_message: __("Creando seguimiento contable…")});
                    dialog.hide();
                    await frm.reload_doc();
                    frappe.set_route("Form", "CN Reconciliation Exception", response.message);
                } finally {
                    busy = false;
                    dialog.get_primary_btn().prop("disabled", false);
                }
            },
        });
        dialog.show();
    } finally {
        frm._cn_creating_exception = false;
    }
}

async function cn_compensate_items_dialog(frm) {
    if (frm.is_dirty()) await frm.save();
    let preview = null;
    let selectedName = null;
    let dateCounterpart = null;
    let managementCounterpart = null;
    let managementChoices = new Map();
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const method = "credinomina_reconciliation.complementary_compensation.";
    const dialog = new frappe.ui.Dialog({
        title: __("Compensar con otra partida"), size: "large",
        fields: [
            {fieldtype: "HTML", fieldname: "instructions", options: `<p>${__("Seleccione el movimiento que compensa esta partida. Puede ser de otro mes. Se conservarán los importes originales.")}</p><p>${__("Consume saldo reduce la disponibilidad desde la fecha efectiva. Vínculo de gestión respalda una gestión ya registrada sin descontarla otra vez. Una reclasificación no acredita una devolución al cliente.")}</p>`},
            {fieldtype: "Link", fieldname: "counterpart", label: __("Partida a compensar"), options: "CN Complementary Item", reqd: 1,
                get_query: () => {
                    const credit = ["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category);
                    return {filters: {name: ["!=", frm.doc.name], docstatus: ["!=", 2],
                        registration_exception: ["is", "not set"],
                        origin_deposit: ["is", "not set"],
                        category: ["not in", ["Ajuste de aplicación", "Diferencia por tolerancia",
                            "Vinculado a depósito",
                            ...(credit ? ["Saldo a favor de la empresa", "Saldo a favor del cliente"] : [])]],
                        ...(credit && frm.doc.employer ? {employer: frm.doc.employer} : {}),
                        ...(credit ? {accounting_source_key: ["is", "set"], source_debit: [">", 0], source_credit: 0} : {}),
                    }};
                },
                async onchange() {
                    const selected = dialog.get_value("counterpart");
                    preview = null;
                    selectedName = null;
                    dialog.fields_dict.summary.$wrapper.empty();
                    dialog.get_primary_btn().prop("disabled", true);
                    if (!selected) {
                        dateCounterpart = null;
                        await dialog.set_value("compensation_date", frm.doc.posting_date || "");
                        return;
                    }
                    if (managementCounterpart !== selected) {
                        const data = (await frappe.call({method: "credinomina_reconciliation.compensation_effect.options", args: {item_name: frm.doc.name, counterpart: selected}})).message || {};
                        if (selected !== dialog.get_value("counterpart")) return;
                        managementChoices = cn_management_choices(data.management || []);
                        managementCounterpart = selected;
                        dialog.set_df_property("credit_effect", "hidden", !data.credit_item);
                        if (!data.credit_item) await dialog.set_value("credit_effect", "Consume saldo");
                        dialog.set_df_property("credit_management_entry", "options", ["", ...managementChoices.keys()]);
                        await dialog.set_value("credit_management_entry", "");
                    }
                    const effect = dialog.get_value("credit_effect") || "Consume saldo";
                    const entry = effect === "Vínculo de gestión" ? managementChoices.get(dialog.get_value("credit_management_entry")) : "";
                    if (effect === "Vínculo de gestión" && !entry) return;
                    const response = await frappe.call({method: method + "preview_compensation", args: {item_name: frm.doc.name, counterpart: selected,
                        credit_effect: effect, credit_management_entry: entry}});
                    if (selected !== dialog.get_value("counterpart") || effect !== (dialog.get_value("credit_effect") || "Consume saldo")
                        || (effect === "Vínculo de gestión" && entry !== managementChoices.get(dialog.get_value("credit_management_entry")))) return;
                    preview = response.message;
                    selectedName = selected;
                    if (dateCounterpart !== selected) {
                        const latestDate = [preview.left.posting_date, preview.right.posting_date].filter(Boolean).sort().pop();
                        await dialog.set_value("compensation_date", latestDate || "");
                        dateCounterpart = selected;
                    }
                    const cards = [preview.left, preview.right].map(item => `<div class="col-sm-6"><div class="well">
                        <strong>${esc(item.name)}</strong><p>${esc(frappe.datetime.str_to_user(item.posting_date))}<br>
                        ${esc(item.employer || __("Empresa sin identificar"))}<br>${__("Asiento")}: ${esc(item.voucher || __("Pendiente"))}</p>
                        ${item.client_name || item.client_number ? `<p>${esc(item.client_name)}${item.client_number ? ` · ${__("Nro. Cliente")}: ${esc(item.client_number)}` : ""}</p>` : ""}
                        <p>${esc(item.description)}</p><div>${__("Original US$")}: ${esc(format_currency(item.original_usd, "USD"))}</div>
                        <div>${__("Compensado US$")}: ${esc(format_currency(item.compensated_usd, "USD"))}</div>
                        <strong>${__(item.is_credit ? "Sin vínculo contable US$" : "Pendiente US$")}: ${esc(format_currency(item.pending_usd, "USD"))}</strong>
                        ${item.is_credit ? `<p class="mt-2">${__("Saldo disponible US$")}: ${esc(format_currency(item.management_pending_usd, "USD"))}</p>` : ""}</div></div>`).join("");
                    dialog.fields_dict.summary.$wrapper.html(`<div class="row">${cards}</div>`);
                    await dialog.set_value("amount_usd", preview.suggested_usd);
                    dialog.get_primary_btn().prop("disabled", preview.suggested_usd <= 0);
                }},
            {fieldtype: "Select", fieldname: "credit_effect", label: __("Tratamiento del saldo"), options: ["Consume saldo", "Vínculo de gestión"], default: "Consume saldo", reqd: 1,
                onchange() { return dialog.fields_dict.counterpart.df.onchange(); }},
            {fieldtype: "Select", fieldname: "credit_management_entry", label: __("Gestión ya registrada"), options: [""],
                depends_on: "eval:doc.credit_effect == 'Vínculo de gestión'", mandatory_depends_on: "eval:doc.credit_effect == 'Vínculo de gestión'",
                onchange() { return dialog.fields_dict.counterpart.df.onchange(); }},
            {fieldtype: "HTML", fieldname: "summary"},
            {fieldtype: "Currency", fieldname: "amount_usd", label: __("Importe a compensar US$"), options: "USD", precision: 2, reqd: 1},
            {fieldtype: "Date", fieldname: "compensation_date", label: __("Fecha de compensación"), default: frm.doc.posting_date, reqd: 1},
            {fieldtype: "Small Text", fieldname: "reason", label: __("Motivo y referencia de la compensación"), reqd: 1},
            {fieldtype: "Check", fieldname: "reviewed", label: __("Verifiqué la contrapartida, empresa, cliente e importe; esta operación no registra una devolución"), reqd: 1},
        ],
        primary_action_label: __("Confirmar compensación"),
        async primary_action(values) {
            if (!preview || selectedName !== values.counterpart || !values.reviewed) return;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                await frappe.call({method: method + "confirm_compensation", args: {item_name: frm.doc.name, counterpart: values.counterpart,
                    amount_usd: values.amount_usd, compensation_date: values.compensation_date, reason: values.reason, request_key: preview.request_key,
                    credit_effect: values.credit_effect || "Consume saldo",
                    credit_management_entry: values.credit_effect === "Vínculo de gestión" ? managementChoices.get(values.credit_management_entry) : ""},
                    freeze: true, freeze_message: __("Registrando compensación en ambas partidas…")});
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __(preview.left.is_credit || preview.right.is_credit
                    ? (values.credit_effect === "Vínculo de gestión" ? "Vínculo contable registrado sin volver a consumir la gestión." : "Compensación registrada. El saldo disponible se redujo; la distribución del depósito se conserva.")
                    : "Compensación registrada en ambas partidas."), indicator: "green"});
            } finally {
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
    dialog.get_primary_btn().prop("disabled", true);
}

async function cn_correct_compensation_date(frm) {
    if (frm._cn_correcting_compensation_date) return;
    const method = "credinomina_reconciliation.complementary_compensation.";
    const itemName = frm.doc.name;
    const response = await frappe.call({method: method + "get_date_correction_candidates", args: {item_name: itemName}});
    const data = response.message || {};
    if (!(data.rows || []).length) return frappe.msgprint(__("No hay compensaciones cuya fecha pueda corregirse."));
    const choices = new Map(data.rows.map((row, index) => [
        `${index + 1}. ${row.counterpart} · ${format_currency(row.amount_usd, "USD")} · ${frappe.datetime.str_to_user(row.compensation_date)}${row.reversed ? " · " + __("Revertida") : ""}`, row.operation_id]));
    const dialog = new frappe.ui.Dialog({title: __("Corregir fecha de compensación"), fields: [
        {fieldtype: "HTML", options: `<p>${__("Se conservará el historial y se registrará la compensación con la fecha correcta en ambas partidas. Si estaba revertida por error de fecha, volverá a quedar compensada por el mismo importe. No registra una devolución ni modifica el depósito.")}</p>`},
        {fieldname: "operation", fieldtype: "Select", label: __("Compensación original"), options: [...choices.keys()], reqd: 1},
        {fieldname: "compensation_date", fieldtype: "Date", label: __("Fecha correcta"), reqd: 1},
        {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo de corrección"), reqd: 1},
    ], primary_action_label: __("Corregir fecha en ambas partidas"), async primary_action(values) {
        if (frm._cn_correcting_compensation_date || !choices.has(values.operation)) return;
        frm._cn_correcting_compensation_date = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: method + "correct_compensation_date", args: {item_name: itemName,
                operation_id: choices.get(values.operation), compensation_date: values.compensation_date,
                reason: values.reason, request_key: data.request_key}, freeze: true,
                freeze_message: __("Corrigiendo la fecha en ambas partidas…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.msgprint(__("Fecha corregida. Reconstruya los reportes preparados para consultar el corte actualizado."));
        } finally {
            frm._cn_correcting_compensation_date = false;
            dialog.get_primary_btn().prop("disabled", false);
        }
    }});
    dialog.show();
}

async function cn_reverse_compensation(frm) {
    if (frm._cn_reversing_compensation) return;
    if (frm.is_dirty()) await frm.save();
    const itemName = frm.doc.name;
    const method = "credinomina_reconciliation.complementary_compensation.";
    const response = await frappe.call({method: method + "get_reversible_compensations", args: {item_name: itemName}});
    if (frm.doc.name !== itemName) return;
    const data = response.message || {};
    if (!(data.rows || []).length) return frappe.msgprint(__("No hay compensaciones pendientes de reversión."));
    const choices = new Map(data.rows.map((row, index) => [
        `${index + 1}. ${row.counterpart} · ${format_currency(row.amount_usd, "USD")} · ${frappe.datetime.str_to_user(row.compensation_date)}`, row.operation_id]));
    const dialog = new frappe.ui.Dialog({title: __("Revertir compensación"), fields: [
        {fieldtype: "HTML", options: `<p>${__("Se conservará la compensación original y se registrará su reversión en ambas partidas. Sus saldos pendientes aumentarán por el importe revertido. No genera depósitos ni asientos en el core.")}</p>`},
        {fieldname: "operation", fieldtype: "Select", label: __("Compensación a revertir"), options: [...choices.keys()], reqd: 1},
        {fieldname: "reversal_date", fieldtype: "Date", label: __("Fecha de reversión"), default: frappe.datetime.get_today(), reqd: 1},
        {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo"), reqd: 1},
    ], primary_action_label: __("Confirmar reversión en ambas partidas"), async primary_action(values) {
        if (frm._cn_reversing_compensation || !choices.has(values.operation)) return;
        frm._cn_reversing_compensation = true;
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: method + "reverse_compensation", args: {item_name: itemName,
                operation_id: choices.get(values.operation), reversal_date: values.reversal_date, reason: values.reason, request_key: data.request_key},
                freeze: true, freeze_message: __("Registrando reversión en ambas partidas…")});
            dialog.hide();
            await frm.reload_doc();
            frappe.show_alert({message: __("Reversión registrada en ambas partidas. El historial original se conserva."), indicator: "green"});
        } finally {frm._cn_reversing_compensation = false; dialog.get_primary_btn().prop("disabled", false);}
    }});
    dialog.show();
}

function cn_compensation_balance_dialog(frm) {
    const dialog = new frappe.ui.Dialog({title: __("Saldo de la partida a fecha"), fields: [
        {fieldtype: "Date", fieldname: "as_of_date", label: __("Fecha de corte"), default: frappe.datetime.get_today(), reqd: 1},
        {fieldtype: "HTML", fieldname: "summary"},
    ], primary_action_label: __("Consultar"), async primary_action(values) {
        const response = await frappe.call({method: "credinomina_reconciliation.complementary_compensation.get_compensation_balance",
            args: {item_name: frm.doc.name, as_of_date: values.as_of_date}});
        const result = response.message;
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        dialog.fields_dict.summary.$wrapper.html(`<p><strong>${esc(__(result.status))}</strong></p>
            <p>${__("Original US$")}: ${esc(format_currency(result.original_usd, "USD"))}<br>
            ${__("Compensado US$")}: ${esc(format_currency(result.compensated_usd, "USD"))}<br>
            <strong>${__("Pendiente US$")}: ${esc(format_currency(result.pending_usd, "USD"))}</strong></p>`);
    }});
    dialog.show();
}

function cn_application_links(doc) {
    const extra = JSON.parse(doc.application_adjustment_history || "[]");
    if (!Array.isArray(extra)) throw new Error("Historial inválido");
    return (doc.related_application ? [{related_application: doc.related_application, related_import: doc.related_import,
        application_adjustment_usd: doc.application_adjustment_usd, client_name: doc.client_name, loan_number: doc.loan_number}] : []).concat(extra);
}

async function cn_render_application_links(frm) {
    const wrapper = frm.fields_dict?.application_links_preview?.$wrapper;
    if (!wrapper) return;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const name = frm.doc.name, sequence = (frm.cn_application_links_sequence || 0) + 1;
    frm.cn_application_links_sequence = sequence;
    try {
        let links = cn_application_links(frm.doc);
        if (name && !frm.doc.__islocal && frm.doc.category === "Ajuste de aplicación") {
            const response = await frappe.call({method: "credinomina_reconciliation.application_adjustment_links.get_detail", args: {item_name: name}});
            if (frm.doc.name !== name || frm.cn_application_links_sequence !== sequence) return;
            links = response.message || [];
        }
        const used = links.reduce((sum, row) => sum + Number(row.application_adjustment_usd || 0), 0);
        wrapper.html(`<p><strong>${__("Distribuido US$")}: ${esc(format_currency(used, "USD"))} · ${__("Remanente US$")}: ${esc(format_currency(Math.abs(frm.doc.amount_usd || 0) - used, "USD"))}</strong></p>
            <div style="overflow-x:auto"><table class="table table-bordered"><thead><tr><th>${__("Aplicación")}</th><th>${__("Importación")}</th><th>${__("Fecha de aplicación")}</th><th>${__("Cliente")}</th><th>${__("Crédito")}</th><th>${__("Ajuste US$")}</th></tr></thead><tbody>${links.map(row => `<tr><td>${esc(row.related_application)}</td><td>${row.related_import ? `<a href="/app/cn-accounting-import/${encodeURIComponent(row.related_import)}">${esc(row.related_import)}</a>` : ""}</td><td>${esc(row.event_date)}</td><td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(format_currency(row.application_adjustment_usd, "USD"))}</td></tr>`).join("")}</tbody></table></div>
            <p class="text-muted">${__(frm.doc.docstatus === 2 ? "Partida cancelada. Se conserva la distribución como evidencia; sus ajustes fueron revertidos." : frm.doc.docstatus === 1 ? "Los vínculos confirmados se conservan. Puede distribuir el remanente entre otras aplicaciones." : "La distribución del borrador no reduce aplicaciones hasta usar Confirmar ajuste.")}</p>`);
    } catch (_) {if (frm.doc.name === name && frm.cn_application_links_sequence === sequence) wrapper.html(`<p class="text-danger">${__("No se pudo leer la distribución. Recargue y revise la partida.")}</p>`);}
}

function cn_application_direction_invalid(doc) {
    return Boolean(doc.accounting_source_key) &&
        (Number(doc.source_debit || 0) !== 0 || Number(doc.source_credit || 0) <= 0);
}

async function cn_distribute_applications(frm, initial = null) {
    if (!frm.doc.employer) {frappe.msgprint(__("Identifique y guarde primero la empresa de la partida.")); return;}
    if (cn_application_direction_invalid(frm.doc)) {
        frappe.msgprint(__("La partida importada debe registrar crédito positivo y débito cero para cancelar una aplicación de débito."));
        return;
    }
    if (frm.is_dirty()) await frm.save();
    const used = cn_application_links(frm.doc).reduce((sum, row) => sum + Number(row.application_adjustment_usd || 0), 0);
    const remaining = Math.round((Math.abs(frm.doc.amount_usd || 0) - (frm.doc.docstatus === 1 ? used : 0)) * 100) / 100;
    if (!(remaining > 0)) {frappe.msgprint(__("La partida no tiene remanente disponible.")); return;}
    let request = 0, busy = false;
    const selected = new Map();
    if (frm.doc.docstatus === 0) for (const link of cn_application_links(frm.doc)) {
        const previous = selected.get(link.related_application)?.amount_usd || 0;
        selected.set(link.related_application, {application: link.related_application, amount_usd: previous + Number(link.application_adjustment_usd || 0)});
    }
    function remember() {
        const rows = dialog.fields_dict.applications.df.data || [];
        for (const row of rows) {
            if (Number(row.amount_usd) < 0) throw new Error(__("Los importes deben ser positivos o cero para omitir la fila."));
            if (Number(row.amount_usd) > 0) selected.set(row.application, {application: row.application, amount_usd: Number(row.amount_usd)});
            else selected.delete(row.application);
        }
    }
    const dialog = new frappe.ui.Dialog({title: __("Distribuir entre aplicaciones"), size: "extra-large", fields: [
        {fieldtype: "HTML", options: `<p><strong>${__("Remanente disponible")}: ${format_currency(remaining, "USD")}</strong><br>${__("Indique el importe para cada aplicación. Deje cero para omitirla. Puede seleccionar aplicaciones de varias importaciones antes de guardar.")}</p>`},
        {fieldtype: "Link", fieldname: "accounting_import", label: __("Importación contable"), options: "CN Accounting Import",
            get_query: () => ({query: "credinomina_reconciliation.accounting_review.application_import_query", filters: {employer: frm.doc.employer}}),
            async onchange() {
                try {remember();} catch (error) {frappe.msgprint(error.message); return;}
                const current = ++request, name = dialog.get_value("accounting_import");
                dialog.fields_dict.applications.df.data = [];
                dialog.fields_dict.applications.grid.refresh();
                if (!name) return;
                const response = await frappe.call({method: "credinomina_reconciliation.accounting_review.application_candidates",
                    args: {item_name: frm.doc.name, accounting_import: name}});
                if (current !== request) return;
                if (initial && name === initial.application.document) {
                    const candidate = (response.message || []).find(row => row.name === initial.application.row_name);
                    if (candidate && initial.proposed_usd > 0 && initial.proposed_usd <= candidate.adjustable_usd) {
                        selected.set(candidate.name, {application: candidate.name, amount_usd: initial.proposed_usd});
                    }
                    initial = null;
                }
                dialog.fields_dict.applications.df.data = (response.message || []).map(row => ({application: row.name,
                    client_name: row.client_name, loan_number: row.loan_number, event_date: row.event_date,
                    adjustable_usd: row.adjustable_usd, amount_usd: selected.get(row.name)?.amount_usd || 0}));
                dialog.fields_dict.applications.grid.refresh();
            }},
        {fieldtype: "Table", fieldname: "applications", label: __("Importe a distribuir por aplicación"),
            cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true, data: [], fields: [
                {fieldtype: "Data", fieldname: "application", hidden: 1, read_only: 1},
                {fieldtype: "Data", fieldname: "client_name", label: __("Cliente"), read_only: 1, in_list_view: 1, columns: 3},
                {fieldtype: "Data", fieldname: "loan_number", label: __("Crédito"), read_only: 1, in_list_view: 1, columns: 2},
                {fieldtype: "Date", fieldname: "event_date", label: __("Fecha"), read_only: 1, in_list_view: 1, columns: 2},
                {fieldtype: "Currency", fieldname: "adjustable_usd", label: __("Disponible US$"), read_only: 1, in_list_view: 1, columns: 1, precision: 2},
                {fieldtype: "Currency", fieldname: "amount_usd", label: __("Ajuste US$"), in_list_view: 1, columns: 2, precision: 2},
            ]},
    ], primary_action_label: __(frm.doc.docstatus === 1 ? "Confirmar distribución adicional" : "Guardar distribución"), async primary_action() {
        if (busy) return;
        try {remember();} catch (error) {frappe.msgprint(error.message); return;}
        const rows = [...selected.values()];
        if (!rows.length || rows.reduce((sum, row) => sum + row.amount_usd, 0) > remaining + .000001) {
            frappe.msgprint(__("Seleccione aplicaciones con importes positivos sin superar el remanente.")); return;
        }
        busy = true;
        dialog.disable_primary_action();
        try {
            await frappe.call({method: "credinomina_reconciliation.application_adjustment_links.distribute", args: {
                item_name: frm.doc.name, modified: frm.doc.modified, rows: JSON.stringify(rows)}, freeze: true});
            dialog.hide();
            await frm.reload_doc();
            frappe.show_alert({message: __(frm.doc.docstatus === 1 ? "Distribución confirmada y saldos actualizados." : "Distribución guardada. Use Confirmar ajuste para aplicarla."), indicator: "green"});
        } finally {busy = false; dialog.enable_primary_action();}
    }});
    dialog.show();
    if (initial) await dialog.set_value("accounting_import", initial.application.document);
}

async function cn_select_original_application(frm, initial = null) {
    if (!frm.doc.employer) { frappe.msgprint(__("Identifique y guarde primero la empresa de la partida.")); return; }
    if (cn_application_direction_invalid(frm.doc)) {
        frappe.msgprint(__("La partida importada debe registrar crédito positivo y débito cero para cancelar una aplicación de débito."));
        return;
    }
    if (frm.is_dirty()) await frm.save();
    let rows = [];
    let request = 0;
    let requestedImport = null;
    const dialog = new frappe.ui.Dialog({
        title: __("Vincular a aplicación"), size: "extra-large",
        fields: [
            {fieldtype: "Link", fieldname: "accounting_import", label: __("Importación contable"),
                options: "CN Accounting Import", reqd: 1, get_query: () => ({
                    query: "credinomina_reconciliation.accounting_review.application_import_query",
                    filters: {employer: frm.doc.employer}}),
                async onchange() {
                    const selected = dialog.get_value("accounting_import") || "";
                    // Link validation can fire again on blur when selecting a row.
                    // Keep the displayed rows and radio selection for the same import.
                    if (selected === requestedImport) return;
                    requestedImport = selected;
                    const currentRequest = ++request;
                    rows = [];
                    dialog.get_primary_btn().prop("disabled", true);
                    dialog.fields_dict.applications.$wrapper.empty();
                    if (!selected) return;
                    let response;
                    try {
                        response = await frappe.call({method: "credinomina_reconciliation.accounting_review.application_candidates",
                            args: {item_name: frm.doc.name, accounting_import: selected}});
                    } catch (error) {
                        if (currentRequest === request) requestedImport = null;
                        throw error;
                    }
                    if (currentRequest !== request || selected !== dialog.get_value("accounting_import")) return;
                    rows = (response.message || []).filter(row => Number(row.adjustable_usd) > 0);
                    const esc = value => frappe.utils.escape_html(String(value ?? ""));
                    if (!rows.length) {
                        dialog.fields_dict.applications.$wrapper.html(`<p class="text-muted">${esc(__("No hay aplicaciones válidas para vincular a esta partida en la importación seleccionada."))}</p>`);
                        return;
                    }
                    dialog.get_primary_btn().prop("disabled", false);
                    dialog.fields_dict.applications.$wrapper.html(`<div style="max-height:50vh;overflow:auto"><table class="table table-bordered"><thead><tr>
                        <th></th><th>${__("Cliente")}</th><th>${__("Crédito")}</th><th>${__("Fecha")}</th><th>${__("Aplicado original US$")}</th><th>${__("Ajustes US$")}</th><th>${__("Aplicado neto US$")}</th><th>${__("Depósitos / reservas US$")}</th><th>${__("Disponible para ajuste US$")}</th><th>${__("Asiento")}</th></tr></thead><tbody>${rows.map((row, index) => `<tr>
                        <td><input type="radio" name="original_application" value="${index}" ${initial?.application.row_name === row.name ? "checked" : ""} aria-label="${esc(__("Seleccionar aplicación"))}"></td>
                        <td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(frappe.datetime.str_to_user(row.event_date))}</td>
                        <td>${esc(format_currency(row.amount_usd, "USD"))}</td><td>${esc(format_currency(row.application_adjustment_usd || 0, "USD"))}</td><td>${esc(format_currency(row.net_applied_usd, "USD"))}</td><td>${esc(format_currency(row.protected_usd || 0, "USD"))}</td><td>${esc(format_currency(row.adjustable_usd || 0, "USD"))}</td><td>${esc(row.voucher)}</td></tr>`).join("")}</tbody></table></div>`);
                }},
            {fieldtype: "HTML", fieldname: "applications"},
            {fieldtype: "Currency", fieldname: "amount_usd", label: __("Importe del ajuste US$"), options: "USD", precision: 2,
                default: initial?.proposed_usd || null,
                description: __("Deje vacío para usar el máximo disponible. Puede indicar un importe menor para un ajuste parcial.")},
        ],
        primary_action_label: __("Vincular"),
        async primary_action() {
            const selected = dialog.fields_dict.applications.$wrapper.find("input:checked").val();
            if (selected === undefined || !rows[Number(selected)]) { frappe.msgprint(__("Seleccione una aplicación.")); return; }
            const row = rows[Number(selected)];
            if (!(Number(row.adjustable_usd) > 0)) {
                frappe.msgprint(__("Esta aplicación no tiene saldo disponible para ajustar. Revise los depósitos asignados o reservados."));
                return;
            }
            const maximum = Math.min(Math.abs(frm.doc.amount_usd || 0), row.adjustable_usd || 0);
            const requested = dialog.fields_dict.amount_usd.$input.val();
            const amount = String(requested ?? "").trim() === "" ? maximum : Number(dialog.get_value("amount_usd"));
            if (!Number.isFinite(amount) || amount <= 0 || amount > maximum) {
                return frappe.msgprint(__("Indique un importe positivo que no supere el disponible de la aplicación ni de la partida."));
            }
            await frm.set_value({related_application: row.name, review_action: "Ajuste de aplicación",
                category: "Ajuste de aplicación", application_adjustment_usd: amount});
            await frm.save();
            dialog.hide();
        },
    });
    dialog.show();
    dialog.get_primary_btn().prop("disabled", true);
    if (initial) await dialog.set_value("accounting_import", initial.application.document);
}
