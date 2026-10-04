async function cn_render_complementary_balance(frm) {
    const wrapper = frm.fields_dict?.financial_overview?.$wrapper;
    if (!wrapper || frm.is_new()) return;
    const name = frm.doc.name;
    const sequence = frm.cn_balance_sequence = (frm.cn_balance_sequence || 0) + 1;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    wrapper.html(`<p class="text-muted">${__("Consultando distribución y saldo…")}</p>`);
    try {
        const {message: value} = await frappe.call({method: "credinomina_reconciliation.complementary_balances.get_balance", args: {item_name: name}});
        if (frm.doc.name !== name || sequence !== frm.cn_balance_sequence) return;
        const cells = [["Importe original US$", value.original_usd], [value.used_label + " US$", value.used_usd], ["Pendiente de conciliar US$", value.pending_usd]];
        wrapper.html(`<div class="row">${cells.map(([label, amount]) => `<div class="col-sm-4"><div class="text-muted">${esc(__(label))}</div><strong>${esc(format_currency(amount, "USD", 2))}</strong></div>`).join("")}</div>
            <p style="margin-top:12px">${__("Conciliación")}: <strong>${esc(__(value.financial_status))}</strong> · ${__("Contabilidad")}: ${esc(__(value.accounting_status))}</p>
            ${value.management_pending_usd == null ? "" : `<p>${__("Gestión del saldo a favor")}: ${esc(__(value.management_status))} · ${__("Pendiente")}: <strong>${esc(format_currency(value.management_pending_usd, "USD", 2))}</strong></p>`}
            ${value.company_receivable_usd ? `<div class="alert alert-warning">${__("CxC a la empresa pendiente de cobro")}: <strong>${esc(format_currency(value.company_receivable_usd, "USD", 2))}</strong><br>${__("El depósito está distribuido, pero este faltante sigue pendiente. El registro contable no equivale a cobro ni compensación.")}</div>` : ""}
            <p class="text-muted">${__("Tener un asiento informado no significa que la partida esté conciliada. Solo se suman distribuciones realizadas, no destinos seleccionados.")}</p>
            ${value.distributions?.length ? `<details><summary>${__("Depósitos que utilizan esta partida")}</summary><table class="table table-bordered"><thead><tr><th>${__("Depósito")}</th><th>${__("Empresa / cliente")}</th><th>${__("US$")}</th></tr></thead><tbody>${value.distributions.map(row => `<tr><td><a href="/app/cn-remittance-allocation/${encodeURIComponent(row.deposit)}">${esc(row.deposit)}</a></td><td>${esc([row.employer, row.client].filter(Boolean).join(" · "))}</td><td>${esc(format_currency(row.amount_usd, "USD", 2))}</td></tr>`).join("")}</tbody></table></details>` : ""}`);
    } catch (_) {
        if (frm.doc.name === name && sequence === frm.cn_balance_sequence) wrapper.html(`<p class="text-danger">${__("No se pudo consultar el saldo. Recargue el formulario; no lo interprete como saldo cero.")}</p>`);
    }
}

frappe.ui.form.on("CN Complementary Item", {
    setup(frm) {
        frm.set_query("registered_deposit", () => ({filters: {docstatus: 1,
            ...(frm.doc.employer && frm.doc.category !== "Saldo a favor del cliente" ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("credit_client", () => ({filters: frm.doc.employer ? {employer: frm.doc.employer} : {}}));
        frm.set_query("credit_assigned_to", () => ({filters: {enabled: 1}}));
        frm.set_query("period", () => ({filters: {status: ["!=", "Cerrado"],
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("employer", "distribution_companies", () => ({filters: {name: ["!=", frm.doc.employer || ""]}}));
    },
    category(frm) {
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
    record_credit_management: cn_record_client_credit_management,
    async credit_client(frm) {
        if (!frm.doc.credit_client || frm.doc.docstatus !== 0) return;
        const client = frm.doc.credit_client;
        const r = await frappe.db.get_value("CN Client", client, ["client_name", "client_number", "employer"]);
        if (frm.doc.credit_client !== client) return;
        await frm.set_value({client_name: r.message.client_name, client_number: r.message.client_number, employer: r.message.employer});
    },
    async registered_deposit(frm) {
        if (!frm.doc.registered_deposit || !["Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)) return;
        const name = frm.doc.registered_deposit;
        const r = await frappe.db.get_value("CN Remittance Allocation", name,
            ["employer", "deposit_reference", "deposit_voucher", "deposit_date"]);
        if (frm.doc.registered_deposit !== name || !["Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)) return;
        await frm.set_value({...(frm.doc.category === "Saldo a favor de la empresa" ? {employer: r.message.employer} : {}), reference: r.message.deposit_reference,
            deposit_voucher: r.message.deposit_voucher, posting_date: frm.doc.posting_date || r.message.deposit_date});
    },
    refresh(frm) {
        cn_render_complementary_balance(frm);
        cn_comp_add_exception_button(frm);
        const automatic = frm.doc.category === "Diferencia por tolerancia";
        const categories = ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación", "Saldo a favor de la empresa", "Saldo a favor del cliente", "Ajuste de aplicación", "Compensación entre partidas"];
        if (frm.doc.accounting_source_key) categories.unshift("Por clasificar");
        if (automatic) categories.push("Diferencia por tolerancia");
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
        cn_client_credit_history(frm);
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
            if (compensated && frm.get_perm(0, "submit") && frm.get_perm(0, "write")) {
                frm.add_custom_button(__("Revertir compensación"), () => cn_reverse_compensation(frm));
            }
        }
        if ((frm.doc.docstatus === 0 || betweenItems) && !frm.is_new() && frm.get_perm(0, "submit")
            && !["Ajuste de aplicación", "Saldo a favor de la empresa", "Saldo a favor del cliente"].includes(frm.doc.category)
            && !frm.doc.related_application && !frm.doc.registered_deposit
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
            if (confirmed && frm.get_perm(0, "submit") && frm.get_perm(0, "write") && frm.doc.credit_history && frm.doc.credit_history !== "[]") {
                frm.add_custom_button(__("Revertir gestión"), () => cn_reverse_credit_management(frm));
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
    if (frm.is_new() || frm.doc.docstatus === 2) return;
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
        {fieldname: "row", fieldtype: "Select", label: __("Fila que ya incluye el excedente"), options: [{value: "", label: "Excedente fuera del detalle por cliente"}, ...rows.map(row => ({value: row.name,
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
        {fieldname: "support_file", fieldtype: "Attach", label: __("Soporte adjunto a la partida"), reqd: 1,
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
            {fieldtype: "HTML", fieldname: "instructions", options: `<p>${__("Seleccione el movimiento que compensa esta partida. Puede ser de otro mes. Se conservarán los importes originales y se reducirá únicamente el saldo pendiente de ambas partidas.")}</p>`},
            {fieldtype: "Link", fieldname: "counterpart", label: __("Partida a compensar"), options: "CN Complementary Item", reqd: 1,
                get_query: () => ({filters: {name: ["!=", frm.doc.name], docstatus: ["!=", 2],
                    category: ["not in", ["Ajuste de aplicación", "Saldo a favor de la empresa", "Saldo a favor del cliente", "Diferencia por tolerancia"]]}}),
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
