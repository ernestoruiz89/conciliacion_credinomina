async function cn_receivable_recovery_dialog(frm) {
    const itemName = frm.doc.name;
    if (frm.is_dirty()) await frm.save();
    if (frm.doc.name !== itemName) return;
    const {message: preview} = await frappe.call({method: "credinomina_reconciliation.receivable_recovery.preview_recovery", args: {item_name: itemName}});
    if (frm.doc.name !== itemName) return;
    const companies = preview.companies.filter(row => Number(row.available_usd) > 0);
    if (!companies.length) {
        frappe.msgprint(__(preview.companies.some(row => row.receivable_usd == null)
            ? "La CxC tiene cobros o compensaciones inconsistentes. Revise los vínculos en el resumen de la partida."
            : "No hay CxC disponible para una nueva liquidación. Revise los cobros y destinos ya vinculados en el resumen de la partida."));
        return;
    }
    let dialog, destinationInfo = null, requestSequence = 0;
    const api = "credinomina_reconciliation.receivable_recovery.";
    const destinationQuery = method => ({query: api + "search_recovery_destinations", filters: {
        item_name: itemName, employer: dialog.get_value("employer"), method,
    }});
    const renderDestination = () => {
        if (!dialog) return;
        const amount = Number(dialog.get_value("amount_usd") || 0);
        const info = destinationInfo;
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        const money = value => esc(format_currency(value, "USD", 2));
        dialog.fields_dict.destination_info.$wrapper.html(info ? `<div class="alert alert-info">
            ${__("Disponible en el destino")}: <strong>${money(info.available_usd)}</strong> ·
            ${__("A utilizar")}: <strong>${money(amount)}</strong><br>
            ${__("Restará en el destino")}: <strong>${money(Number(info.available_usd) - amount)}</strong> ·
            ${__("CxC restante")}: <strong>${money(Number(info.receivable_usd) - amount)}</strong></div>`
            : `<p class="text-muted">${__("Seleccione un movimiento elegible para ver su disponible y el saldo posterior. Los saldos se vuelven a verificar al confirmar.")}</p>`);
        dialog.get_primary_btn().prop("disabled", !info || amount <= 0 || amount > Number(info.available_usd) || amount > Number(info.receivable_usd));
    };
    const updateDestination = async () => {
        if (!dialog) return;
        const sequence = ++requestSequence;
        destinationInfo = null;
        renderDestination();
        const method = dialog.get_value("method");
        const destination = dialog.get_value(method === "Depósito" ? "deposit" : "counterpart");
        if (!destination) return;
        try {
            const {message} = await frappe.call({method: api + "get_recovery_destination", args: {
                item_name: itemName, employer: dialog.get_value("employer"), method, destination,
            }});
            if (sequence !== requestSequence) return;
            destinationInfo = message;
            await dialog.set_value("amount_usd", message.suggested_usd);
            renderDestination();
        } catch (error) {
            if (sequence === requestSequence) { destinationInfo = null; renderDestination(); }
        }
    };
    const updateCompany = () => {
        if (!dialog) return;
        const company = companies.find(row => row.employer === dialog.get_value("employer"));
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        dialog.fields_dict.cxc_info.$wrapper.html(`<div class="alert alert-info"><strong>${esc([company?.employer, company?.subcategory_effect, company?.client_name, company?.loan_number].filter(Boolean).join(" · "))}</strong> · ${esc(__("CxC disponible US$"))}: <strong>${esc(format_currency(company?.available_usd || 0, "USD", 2))}</strong><br>${esc(__("La distribución del depósito original se conserva. Solo un depósito o una compensación vinculada liquida esta CxC; registrar el asiento en el core no la cobra."))}</div>`);
    };
    dialog = new frappe.ui.Dialog({title: __("Aplicar cobro / Compensar CxC"), size: "large", fields: [
        {fieldname: "cxc_info", fieldtype: "HTML"},
        {fieldname: "recovery_section", fieldtype: "Section Break", label: __("Liquidación de la CxC")},
        {fieldname: "employer", fieldtype: "Select", label: __("Empresa de la CxC"), options: companies.map(row => row.employer), default: companies[0].employer, reqd: 1,
            onchange: () => {
                if (!dialog) return;
                dialog.set_value("amount_usd", companies.find(row => row.employer === dialog.get_value("employer"))?.available_usd || 0);
                dialog.set_value("deposit", null);
                dialog.set_value("counterpart", null);
                updateCompany();
                updateDestination();
            }},
        {fieldname: "amount_usd", fieldtype: "Currency", label: __("Importe a liquidar US$"), options: "USD", precision: 2, default: companies[0].available_usd, reqd: 1, onchange: renderDestination},
        {fieldname: "recovery_column", fieldtype: "Column Break"},
        {fieldname: "method", fieldtype: "Select", label: __("Medio de liquidación"), options: ["Depósito", "Compensación"], default: "Depósito", reqd: 1, onchange: updateDestination},
        {fieldname: "recovery_date", fieldtype: "Date", label: __("Fecha de liquidación"), default: frappe.datetime.get_today(), reqd: 1},
        {fieldname: "destination_section", fieldtype: "Section Break", label: __("Movimiento que liquida el saldo")},
        {fieldname: "deposit", fieldtype: "Link", label: __("Depósito con dinero disponible"), options: "CN Remittance Allocation", depends_on: "eval:doc.method == 'Depósito'", mandatory_depends_on: "eval:doc.method == 'Depósito'", get_query: () => destinationQuery("Depósito"), onchange: updateDestination},
        {fieldname: "counterpart", fieldtype: "Link", label: __("Partida de crédito para compensar"), options: "CN Complementary Item", depends_on: "eval:doc.method == 'Compensación'", mandatory_depends_on: "eval:doc.method == 'Compensación'", get_query: () => destinationQuery("Compensación"), onchange: updateDestination},
        {fieldname: "destination_info", fieldtype: "HTML"},
        {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo y referencia de la gestión"), reqd: 1},
    ], primary_action_label: __("Aplicar liquidación"), async primary_action(values) {
        if (frm.doc.name !== itemName) {
            dialog.hide();
            frappe.msgprint(__("El formulario cambió. Abra la liquidación desde la partida que desea gestionar."));
            return;
        }
        const limit = companies.find(row => row.employer === values.employer)?.available_usd || 0;
        if (!destinationInfo || !(Number(values.amount_usd) > 0) || Number(values.amount_usd) > Number(limit) || Number(values.amount_usd) > Number(destinationInfo.available_usd)) {
            frappe.msgprint(__("El importe debe ser positivo y no superar la CxC disponible de la empresa."));
            return;
        }
        dialog.get_primary_btn().prop("disabled", true);
        try {
            await frappe.call({method: "credinomina_reconciliation.receivable_recovery.apply_recovery", args: {
                item_name: itemName, employer: values.employer, method: values.method,
                destination: values.method === "Depósito" ? values.deposit : values.counterpart,
                amount_usd: values.amount_usd, recovery_date: values.recovery_date, reason: values.reason, request_key: preview.request_key,
            }, freeze: true, freeze_message: __("Aplicando liquidación y verificando saldos…")});
            dialog.hide();
            if (frm.doc.name === itemName) await frm.reload_doc();
            frappe.show_alert({message: __("Liquidación vinculada. El saldo de CxC fue actualizado."), indicator: "green"});
        } finally { dialog.get_primary_btn().prop("disabled", false); }
    }});
    dialog.show();
    updateCompany();
    renderDestination();
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
                <div class="mt-2">${__("Registrar el asiento en el core no liquida la deuda. Use Aplicar cobro / Compensar CxC para vincular su liquidación.")}</div></div>` : ""}
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

function cn_classify_pending(frm) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const choices = [];
    const add = (label, hint, action) => choices.push({label, hint, action});
    const draft = frm.doc.docstatus === 0 && !frm.doc.receivable_origin && !(frm.doc.compensations || []).length;
    if (draft) {
        add("Identificar empresa o cliente", "Complete los vínculos sin modificar la evidencia contable original.", () => frm.scroll_to_field("employer"));
        if (!["Aplicación de pago", "ND de Aplicación de pago"].includes(frm.doc.accounting_classification)
                && !["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)) add("Preparar partida de depósito", "Complete concepto, empresa, referencia e importe revisado. Preparar no distribuye dinero.", async () => {
            await frm.set_value("review_action", "Partida de depósito");
            frm.scroll_to_field("category");
        });
    }
    for (const [label, hint, action] of [
        ["Vincular a aplicación", "Ajuste o reversión total/parcial del core. Seleccione la aplicación real.", () => cn_select_original_application(frm)],
        ["Compensar con otra partida", "Vincule la contrapartida; no basta con cambiar el concepto.", () => cn_compensate_items_dialog(frm)],
        ["Aplicar cobro / Compensar CxC", "Elija el depósito o contrapartida que liquida la cuenta por cobrar.", () => cn_receivable_recovery_dialog(frm)],
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
        : choices.some(item => item.label === "Aplicar cobro / Compensar CxC")
            ? "Aplicar cobro / Compensar CxC" : choices[0].label;
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
        help = "Conserve el importe de la partida. Importe del ajuste US$ indica cuánto reduce la aplicación seleccionada y debe ser positivo; el vínculo confirmado determina el efecto.";
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
            frm.add_custom_button(__("Aplicar cobro / Compensar CxC"), () => cn_receivable_recovery_dialog(frm));
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
                : __("Ajuste automático por tolerancia. No requiere asiento por defecto. Si necesita registrarlo en el core, use Crear excepción; su vigencia se controla al conciliar."), "blue");
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
        if (!frm.is_new() && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Clasificar pendiente"), () => cn_classify_pending(frm));
        }
        const compensated = (frm.doc.compensations || []).length > 0;
        const betweenItems = frm.doc.category === "Compensación entre partidas";
        const documentedCredit = ["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(frm.doc.category)
            && frm.doc.docstatus === 1 && frm.doc.result === "Saldo a favor documentado" && !!frm.doc.registered_deposit;
        for (const field of ["category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "period", "client_number", "loan_number", "description"]) {
            frm.set_df_property(field, "read_only", compensated || frm.doc.receivable_origin ? 1 : 0);
        }
        if (compensated) frm.set_df_property("reference", "read_only", 1);
        if (!frm.is_new() && (betweenItems || compensated)) {
            frm.add_custom_button(__("Consultar saldo a fecha"), () => cn_compensation_balance_dialog(frm));
            if (compensated && frm.get_perm(0, "submit") && frm.get_perm(0, "write")) {
                frm.add_custom_button(__("Revertir compensación"), () => cn_reverse_compensation(frm));
            }
        }
        if ((documentedCredit || ((frm.doc.docstatus === 0 || betweenItems)
            && !["Ajuste de aplicación", "Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)
            && !frm.doc.registered_deposit)) && !frm.is_new() && frm.get_perm(0, "submit") && frm.get_perm(0, "write")
            && !frm.doc.related_application && !frm.doc.registration_exception
            && (!compensated || frm.doc.compensation_pending_usd > 0)) {
            frm.add_custom_button(__("Compensar con otra partida"), () => cn_compensate_items_dialog(frm));
        }
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
                frm.add_custom_button(__("Documentar devolución o aplicación"), () => cn_record_client_credit_management(frm));
                frm.change_custom_button_type?.(__("Documentar devolución o aplicación"), null, "primary");
            }
            if (confirmed && frm.get_perm(0, "submit") && frm.get_perm(0, "write") && frm.doc.credit_history && frm.doc.credit_history !== "[]") {
                frm.add_custom_button(__("Revertir gestión"), () => cn_reverse_credit_management(frm), __("Más acciones"));
            }
            frm.dashboard.set_headline_alert(confirmed
                ? __("Saldo a favor documentado. Gestión: {0}. Registrar gestión documenta una devolución o aplicación externa; no genera pagos ni asientos y no libera efectivo del depósito original.", [frm.doc.credit_management_status || "Pendiente"])
                : __("Identifique el beneficiario y depósito del excedente. Indique importe positivo, motivo, responsable y fecha compromiso. Si esta partida ya fue importada, reclasifíquela aquí; no cree otra copia."), "blue");
            return;
        }
        if (betweenItems) {
            frm.dashboard.set_headline_alert(__("{0}. Use Compensar con otra partida para confirmar nuevas compensaciones. No registra depósitos ni modifica aplicaciones. El historial conserva ambas partidas.",
                [frm.doc.compensation_status || __("Sin compensar")]), frm.doc.compensation_pending_usd === 0 && compensated ? "green" : "orange");
            return;
        }
        if (frm.doc.accounting_source_key && frm.doc.docstatus === 0) {
            if (frm.doc.review_action === "Partida de depósito") {
                frm.dashboard.set_headline_alert(__("Partida de depósito: revise concepto, referencia, período e importe y signo. Guarde y confirme para poder agregarla a los destinos de un depósito. No reduce ninguna aplicación."), "blue");
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
    wrapper.html(history.length ? `<div style="overflow:auto"><table class="table table-bordered"><thead><tr>
        <th>${__("Fecha")}</th><th>${__("Gestión")}</th><th>${__("US$")}</th><th>${__("Referencia")}</th><th>${__("Soporte")}</th><th>${__("Registrado por")}</th><th>${__("Observaciones")}</th></tr></thead><tbody>
        ${history.map(row => `<tr><td>${esc(frappe.datetime.str_to_user(row.fecha))}</td><td>${esc(row.tratamiento)}</td><td>${esc(format_currency(row.importe_usd, "USD"))}</td><td>${esc(row.referencia)}</td><td>${proof(row.soporte)}</td><td>${esc(row.usuario)}</td><td>${esc(row.observaciones)}</td></tr>`).join("")}</tbody></table></div>`
        : `<p class="text-muted">${__("Sin gestiones documentadas. Clasificar el saldo no significa que ya fue devuelto o aplicado.")}</p>`);
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
        {fieldtype: "HTML", options: `<p>${__("Corrige únicamente el seguimiento registrado en esta app. Conserva el registro original y su soporte. No revierte pagos en el core ni libera dinero del depósito.")}</p>`},
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
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const method = "credinomina_reconciliation.complementary_compensation.";
    const dialog = new frappe.ui.Dialog({
        title: __("Compensar con otra partida"), size: "large",
        fields: [
            {fieldtype: "HTML", fieldname: "instructions", options: `<p>${__("Seleccione el movimiento que compensa esta partida. Puede ser de otro mes. Se conservarán los importes originales.")}</p><p>${__("Para un saldo a favor, seleccione su contrapartida importada del core. Solo se vincula la evidencia contable: no cambia la distribución del depósito ni el pendiente de devolución. Registre la devolución por separado y una excepción si requiere seguimiento adicional.")}</p>`},
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
                    if (!selected) return;
                    const response = await frappe.call({method: method + "preview_compensation", args: {item_name: frm.doc.name, counterpart: selected}});
                    if (selected !== dialog.get_value("counterpart")) return;
                    preview = response.message;
                    selectedName = selected;
                    const cards = [preview.left, preview.right].map(item => `<div class="col-sm-6"><div class="well">
                        <strong>${esc(item.name)}</strong><p>${esc(frappe.datetime.str_to_user(item.posting_date))}<br>
                        ${esc(item.employer || __("Empresa sin identificar"))}<br>${__("Asiento")}: ${esc(item.voucher || __("Pendiente"))}</p>
                        ${item.client_name || item.client_number ? `<p>${esc(item.client_name)}${item.client_number ? ` · ${__("Nro. Cliente")}: ${esc(item.client_number)}` : ""}</p>` : ""}
                        <p>${esc(item.description)}</p><div>${__("Original US$")}: ${esc(format_currency(item.original_usd, "USD"))}</div>
                        <div>${__("Compensado US$")}: ${esc(format_currency(item.compensated_usd, "USD"))}</div>
                        <strong>${__(item.is_credit ? "Sin vínculo contable US$" : "Pendiente US$")}: ${esc(format_currency(item.pending_usd, "USD"))}</strong>
                        ${item.is_credit ? `<p class="mt-2">${__("Pendiente de gestión US$ (no cambia)")}: ${esc(format_currency(item.management_pending_usd, "USD"))}</p>` : ""}</div></div>`).join("");
                    dialog.fields_dict.summary.$wrapper.html(`<div class="row">${cards}</div>`);
                    await dialog.set_value("amount_usd", preview.suggested_usd);
                    dialog.get_primary_btn().prop("disabled", preview.suggested_usd <= 0);
                }},
            {fieldtype: "HTML", fieldname: "summary"},
            {fieldtype: "Currency", fieldname: "amount_usd", label: __("Importe a compensar US$"), options: "USD", precision: 2, reqd: 1},
            {fieldtype: "Date", fieldname: "compensation_date", label: __("Fecha de compensación"), default: frappe.datetime.get_today(), reqd: 1},
            {fieldtype: "Small Text", fieldname: "reason", label: __("Motivo y referencia de la compensación"), reqd: 1},
            {fieldtype: "Check", fieldname: "reviewed", label: __("Verifiqué la contrapartida, empresa, cliente e importe; esta operación no registra una devolución"), reqd: 1},
        ],
        primary_action_label: __("Confirmar compensación"),
        async primary_action(values) {
            if (!preview || selectedName !== values.counterpart || !values.reviewed) return;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                await frappe.call({method: method + "confirm_compensation", args: {item_name: frm.doc.name, counterpart: values.counterpart,
                    amount_usd: values.amount_usd, compensation_date: values.compensation_date, reason: values.reason, request_key: preview.request_key},
                    freeze: true, freeze_message: __("Registrando compensación en ambas partidas…")});
                dialog.hide();
                await frm.reload_doc();
                frappe.show_alert({message: __(preview.left.is_credit || preview.right.is_credit
                    ? "Vínculo contable registrado. La distribución del depósito y la gestión del saldo a favor no cambiaron."
                    : "Compensación registrada en ambas partidas."), indicator: "green"});
            } finally {
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
    dialog.get_primary_btn().prop("disabled", true);
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

async function cn_select_original_application(frm) {
    if (!frm.doc.employer) { frappe.msgprint(__("Identifique y guarde primero la empresa de la partida.")); return; }
    if (frm.is_dirty()) await frm.save();
    let rows = [];
    let request = 0;
    const dialog = new frappe.ui.Dialog({
        title: __("Vincular a aplicación"), size: "extra-large",
        fields: [
            {fieldtype: "Link", fieldname: "accounting_import", label: __("Importación contable"),
                options: "CN Accounting Import", reqd: 1, get_query: () => ({
                    query: "credinomina_reconciliation.accounting_review.application_import_query",
                    filters: {employer: frm.doc.employer}}),
                async onchange() {
                    const currentRequest = ++request;
                    const selected = dialog.get_value("accounting_import");
                    rows = [];
                    dialog.get_primary_btn().prop("disabled", true);
                    dialog.fields_dict.applications.$wrapper.empty();
                    if (!selected) return;
                    const response = await frappe.call({method: "credinomina_reconciliation.accounting_review.application_candidates",
                        args: {item_name: frm.doc.name, accounting_import: selected}});
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
                        <td><input type="radio" name="original_application" value="${index}" aria-label="${esc(__("Seleccionar aplicación"))}"></td>
                        <td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(frappe.datetime.str_to_user(row.event_date))}</td>
                        <td>${esc(format_currency(row.amount_usd, "USD"))}</td><td>${esc(format_currency(row.application_adjustment_usd || 0, "USD"))}</td><td>${esc(format_currency(row.net_applied_usd, "USD"))}</td><td>${esc(format_currency(row.protected_usd || 0, "USD"))}</td><td>${esc(format_currency(row.adjustable_usd || 0, "USD"))}</td><td>${esc(row.voucher)}</td></tr>`).join("")}</tbody></table></div>`);
                }},
            {fieldtype: "HTML", fieldname: "applications"},
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
            await frm.set_value({related_application: row.name, review_action: "Ajuste de aplicación",
                category: "Ajuste de aplicación", application_adjustment_usd: Math.min(Math.abs(frm.doc.amount_usd || 0), row.adjustable_usd || 0)});
            await frm.save();
            dialog.hide();
        },
    });
    dialog.show();
    dialog.get_primary_btn().prop("disabled", true);
}
