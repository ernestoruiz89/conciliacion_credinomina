frappe.ui.form.on("CN Complementary Item", {
    setup(frm) {
        frm.set_query("registered_deposit", () => ({filters: {docstatus: 1,
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("period", () => ({filters: {status: ["!=", "Cerrado"],
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
    },
    category(frm) {
        if (frm.doc.category === "Compensación entre partidas" && frm.doc.review_action !== frm.doc.category) {
            frm.set_value("review_action", frm.doc.category);
        }
        if (frm.doc.category === "Ajuste de aplicación" && frm.doc.review_action !== "Ajuste de aplicación") {
            frm.set_value("review_action", "Ajuste de aplicación");
        }
        const credit = frm.doc.category === "Saldo a favor de la empresa";
        // Keep invalid prefilled identities visible so the user can clear them.
        ["client_number", "loan_number", "installment_number"].forEach(f => frm.toggle_display(f, !credit || !!frm.doc[f]));
        frm.set_df_property("reference", "read_only", credit);
    },
    review_action(frm) {
        if (frm.doc.review_action === "Ajuste de aplicación") frm.set_value("category", "Ajuste de aplicación");
        if (frm.doc.review_action === "Compensación entre partidas") frm.set_value("category", "Compensación entre partidas");
    },
    async registered_deposit(frm) {
        if (!frm.doc.registered_deposit || frm.doc.category !== "Saldo a favor de la empresa") return;
        const name = frm.doc.registered_deposit;
        const r = await frappe.db.get_value("CN Remittance Allocation", name,
            ["employer", "deposit_reference", "deposit_voucher", "deposit_date"]);
        if (frm.doc.registered_deposit !== name || frm.doc.category !== "Saldo a favor de la empresa") return;
        await frm.set_value({employer: r.message.employer, reference: r.message.deposit_reference,
            deposit_voucher: r.message.deposit_voucher, posting_date: frm.doc.posting_date || r.message.deposit_date});
    },
    refresh(frm) {
        const automatic = frm.doc.category === "Diferencia por tolerancia";
        const categories = ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Saldo a favor de la empresa", "Ajuste de aplicación", "Compensación entre partidas"];
        if (frm.doc.accounting_source_key) categories.unshift("Por clasificar");
        if (automatic) categories.push("Diferencia por tolerancia");
        frm.set_df_property("category", "options", categories.join("\n"));
        if (automatic) {
            if (!frm._cn_tolerance_read_only) frm._cn_tolerance_read_only = new Map(
                frm.fields.map(field => [field.df.fieldname, field.df.read_only || 0]));
            frm.fields.forEach(field => frm.set_df_property(field.df.fieldname, "read_only", 1));
            frm.disable_save();
            frm.dashboard.set_headline_alert(__("Ajuste automático por tolerancia. No requiere asiento contable; su vigencia se controla al conciliar."), "blue");
            return;
        }
        if (frm._cn_tolerance_read_only) {
            for (const [fieldname, readOnly] of frm._cn_tolerance_read_only) frm.set_df_property(fieldname, "read_only", readOnly);
            delete frm._cn_tolerance_read_only;
            if (frm.get_perm(0, "write")) frm.enable_save();
        }
        frm.trigger("category");
        if (frm.doc.registered_deposit) frm.add_custom_button(__("Abrir depósito"),
            () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.registered_deposit));
        if (frm.doc.docstatus === 2) return;
        const compensated = (frm.doc.compensations || []).length > 0;
        const betweenItems = frm.doc.category === "Compensación entre partidas";
        for (const field of ["category", "review_action", "amount", "currency", "fx_rate", "posting_date", "employer", "period", "client_number", "loan_number", "description"]) {
            frm.set_df_property(field, "read_only", compensated ? 1 : 0);
        }
        if (compensated) frm.set_df_property("reference", "read_only", 1);
        if (!frm.is_new() && (betweenItems || compensated)) {
            frm.add_custom_button(__("Consultar saldo a fecha"), () => cn_compensation_balance_dialog(frm));
        }
        if ((frm.doc.docstatus === 0 || betweenItems) && !frm.is_new() && frm.get_perm(0, "submit")
            && !["Ajuste de aplicación", "Saldo a favor de la empresa"].includes(frm.doc.category)
            && !frm.doc.related_application && !frm.doc.registered_deposit
            && (!compensated || frm.doc.compensation_pending_usd > 0)) {
            frm.add_custom_button(__("Compensar con otra partida"), () => cn_compensate_items_dialog(frm));
        }
        if (frm.doc.docstatus === 0 && !frm.is_new()) {
            if (!betweenItems && !compensated) frm.add_custom_button(__("Vincular a aplicación"), () => cn_select_original_application(frm));
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
        if (betweenItems) {
            frm.dashboard.set_headline_alert(__("{0}. Use Compensar con otra partida para confirmar nuevas compensaciones. No registra depósitos ni modifica aplicaciones. El historial conserva ambas partidas.",
                [frm.doc.compensation_status || __("Sin compensar")]), frm.doc.compensation_pending_usd === 0 && compensated ? "green" : "orange");
            return;
        }
        if (frm.doc.accounting_source_key && frm.doc.docstatus === 0) {
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

async function cn_compensate_items_dialog(frm) {
    if (frm.is_dirty()) await frm.save();
    let preview = null;
    let selectedName = null;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const method = "credinomina_reconciliation.complementary_compensation.";
    const dialog = new frappe.ui.Dialog({
        title: __("Compensar con otra partida"), size: "large",
        fields: [
            {fieldtype: "HTML", fieldname: "instructions", options: `<p>${__("Seleccione el movimiento que compensa esta partida. Puede ser de otro mes. Se conservarán los importes originales y se reducirá únicamente el saldo pendiente de ambas partidas.")}</p>`},
            {fieldtype: "Link", fieldname: "counterpart", label: __("Partida a compensar"), options: "CN Complementary Item", reqd: 1,
                get_query: () => ({filters: {name: ["!=", frm.doc.name], docstatus: ["!=", 2],
                    category: ["not in", ["Ajuste de aplicación", "Saldo a favor de la empresa", "Diferencia por tolerancia"]]}}),
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
                        <p>${esc(item.description)}</p><div>${__("Original US$")}: ${esc(format_currency(item.original_usd, "USD"))}</div>
                        <div>${__("Compensado US$")}: ${esc(format_currency(item.compensated_usd, "USD"))}</div>
                        <strong>${__("Pendiente US$")}: ${esc(format_currency(item.pending_usd, "USD"))}</strong></div></div>`).join("");
                    dialog.fields_dict.summary.$wrapper.html(`<div class="row">${cards}</div>`);
                    await dialog.set_value("amount_usd", preview.suggested_usd);
                    dialog.get_primary_btn().prop("disabled", preview.suggested_usd <= 0);
                }},
            {fieldtype: "HTML", fieldname: "summary"},
            {fieldtype: "Currency", fieldname: "amount_usd", label: __("Importe a compensar US$"), options: "USD", precision: 2, reqd: 1},
            {fieldtype: "Date", fieldname: "compensation_date", label: __("Fecha de compensación"), default: frappe.datetime.get_today(), reqd: 1},
            {fieldtype: "Small Text", fieldname: "reason", label: __("Motivo y referencia de la reversión"), reqd: 1},
            {fieldtype: "Check", fieldname: "reviewed", label: __("Verifiqué que ambas partidas se compensan, incluso si no tienen empresa identificada"), reqd: 1},
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
                frappe.show_alert({message: __("Compensación registrada en ambas partidas."), indicator: "green"});
            } finally {
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
    dialog.get_primary_btn().prop("disabled", true);
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
    const dialog = new frappe.ui.Dialog({
        title: __("Vincular a aplicación"), size: "extra-large",
        fields: [
            {fieldtype: "Link", fieldname: "accounting_import", label: __("Importación contable"),
                options: "CN Accounting Import", reqd: 1, get_query: () => ({filters: {employer: frm.doc.employer}}),
                async onchange() {
                    const selected = dialog.get_value("accounting_import");
                    rows = [];
                    dialog.fields_dict.applications.$wrapper.empty();
                    if (!selected) return;
                    const response = await frappe.call({method: "credinomina_reconciliation.accounting_review.application_candidates",
                        args: {item_name: frm.doc.name, accounting_import: selected}});
                    if (selected !== dialog.get_value("accounting_import")) return;
                    rows = response.message || [];
                    const esc = value => frappe.utils.escape_html(String(value ?? ""));
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
}
