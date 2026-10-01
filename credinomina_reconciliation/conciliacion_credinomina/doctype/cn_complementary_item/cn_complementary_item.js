frappe.ui.form.on("CN Complementary Item", {
    setup(frm) {
        frm.set_query("registered_deposit", () => ({filters: {docstatus: 1,
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("period", () => ({filters: {status: ["!=", "Cerrado"],
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
    },
    category(frm) {
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
        const categories = ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Saldo a favor de la empresa", "Ajuste de aplicación"];
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
        if (frm.doc.docstatus === 0 && !frm.is_new()) {
            frm.add_custom_button(__("Vincular a aplicación"), () => cn_select_original_application(frm));
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
                        <th></th><th>${__("Cliente")}</th><th>${__("Crédito")}</th><th>${__("Fecha")}</th><th>${__("Aplicado original US$")}</th><th>${__("Ajustes US$")}</th><th>${__("Aplicado neto US$")}</th><th>${__("Asiento")}</th></tr></thead><tbody>${rows.map((row, index) => `<tr>
                        <td><input type="radio" name="original_application" value="${index}" aria-label="${esc(__("Seleccionar aplicación"))}"></td>
                        <td>${esc(row.client_name)}</td><td>${esc(row.loan_number)}</td><td>${esc(frappe.datetime.str_to_user(row.event_date))}</td>
                        <td>${esc(format_currency(row.amount_usd, "USD"))}</td><td>${esc(format_currency(row.application_adjustment_usd || 0, "USD"))}</td><td>${esc(format_currency(row.net_applied_usd, "USD"))}</td><td>${esc(row.voucher)}</td></tr>`).join("")}</tbody></table></div>`);
                }},
            {fieldtype: "HTML", fieldname: "applications"},
        ],
        primary_action_label: __("Vincular"),
        async primary_action() {
            const selected = dialog.fields_dict.applications.$wrapper.find("input:checked").val();
            if (selected === undefined || !rows[Number(selected)]) { frappe.msgprint(__("Seleccione una aplicación.")); return; }
            const row = rows[Number(selected)];
            await frm.set_value({related_application: row.name, review_action: "Ajuste de aplicación",
                category: "Ajuste de aplicación", application_adjustment_usd: Math.min(Math.abs(frm.doc.amount_usd || 0), row.net_applied_usd || 0)});
            await frm.save();
            dialog.hide();
        },
    });
    dialog.show();
}
