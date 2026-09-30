frappe.ui.form.on("CN Remittance Allocation", {
    setup(frm) {
        // Imported rows remain intact; only the credit column is editable.
        frm.fields_dict.detail_rows.grid.df.cannot_add_rows = true;
        frm.fields_dict.detail_rows.grid.df.cannot_delete_rows = true;
        frm.set_query("bank_account", () => ({ filters: { active: 1 } }));
        frm.set_query("detail_period", () => ({ filters: { employer: frm.doc.employer, status: ["!=", "Cerrado"] } }));
    },
    refresh(frm) {
        renderRemittanceOverview(frm);
        renderRemittanceAllocations(frm);
        toggleRemittanceDetailActions(frm);
        frm.toggle_display("select_pending_targets", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        frm.toggle_display("create_complementary", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        frm.toggle_display("link_detail_targets", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write") &&
            !!(frm.doc.detail_rows || []).length && !!(frm.doc.targets || []).length);
        frm.toggle_display("select_detail_credit", !frm.is_new() && frm.doc.docstatus !== 2 &&
            !!frm.get_perm(0, "write") && !!(frm.doc.detail_rows || []).length);
        frm.add_custom_button(__("Plantilla de detalle del depósito"), () => downloadRemittanceTemplate(frm), __("Plantillas"));
        if (!frm.is_new() && frm.doc.docstatus === 0 && frm.get_perm(0, "submit")) {
            frm.add_custom_button(__("Confirmar depósito"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.savesubmit();
            });
        }
        if (!frm.is_new() && frm.doc.docstatus === 1 && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Conciliar"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.reconcile_remittance",
                    args: { remittance_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Conciliando depósito…"),
                });
                await frm.reload_doc();
            }, __("Conciliación"));
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
    detail_file(frm) { toggleRemittanceDetailActions(frm); renderRemittanceOverview(frm); },
    support_file: toggleRemittanceDetailActions,
    amount_usd: renderRemittanceOverview,
    employer: renderRemittanceOverview,
    detail_period: renderRemittanceOverview,
    notes: renderRemittanceOverview,
    deposit_amount: updateUsdEquivalent,
    deposit_currency: updateUsdEquivalent,
    fx_rate: updateUsdEquivalent,
    deposit_date: updateUsdEquivalent,
    async bank_account(frm) {
        const document = frm.doc;
        const account = document.bank_account;
        if (!account || document.docstatus !== 0) return;
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

frappe.ui.form.on("CN Remittance Detail", {
    loan_number(frm) {
        frm.set_value("result", "Pendiente");
        frm.set_value("detail_status", "Cargado; pendiente de conciliación");
        frappe.show_alert({message: __("Guarde y use Conciliar. Revise los destinos manuales si cambió el crédito."), indicator: "orange"});
    },
});

async function createRemittanceComplementary(frm) {
    if (frm.is_new() || frm.is_dirty()) await frm.save();
    let busy = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Crear partida complementaria"), size: "large",
        fields: [
            {fieldtype: "HTML", options: `<p>Use un importe positivo para un depósito mayor que la aplicación y negativo cuando falta depósito.
                Por ejemplo: aplicado US$90, depósito US$100 → +US$10; aplicado US$100, depósito US$90 → −US$10.
                Si usa un importe negativo, agréguelo antes de seleccionar las aplicaciones restantes.</p>`},
            {fieldname: "category", fieldtype: "Select", label: __("Concepto"), options: "Cobranza administrativa\nOtros ingresos\nAjuste de conciliación", default: "Ajuste de conciliación", reqd: 1},
            {fieldname: "posting_date", fieldtype: "Date", label: __("Fecha de la partida"), default: frm.doc.deposit_date, reqd: 1},
            {fieldtype: "Column Break"},
            {fieldname: "currency", fieldtype: "Select", label: __("Moneda"), options: "USD\nNIO", default: "USD", reqd: 1},
            {fieldname: "amount", fieldtype: "Currency", options: "currency", label: __("Importe (+ / −)"), precision: 2, reqd: 1},
            {fieldname: "fx_rate", fieldtype: "Float", label: __("Tipo de cambio C$/US$"), precision: 8, default: frm.doc.fx_rate,
                depends_on: "eval:doc.currency === 'NIO'", mandatory_depends_on: "eval:doc.currency === 'NIO'"},
            {fieldtype: "Section Break", label: __("Identificación y seguimiento")},
            {fieldname: "description", fieldtype: "Small Text", label: __("Justificación"), reqd: 1},
            {fieldname: "voucher", fieldtype: "Data", label: __("Asiento contable (opcional)"), description: __("Sin asiento quedará pendiente de registro contable. Puede completarlo después en la partida.")},
            {fieldname: "voucher_line", fieldtype: "Data", label: __("Línea del asiento")},
            {fieldtype: "Section Break", label: __("Vínculo con cliente (opcional)"), collapsible: 1},
            {fieldname: "period", fieldtype: "Link", options: "CN Reconciliation Period", label: __("Período"),
                get_query: () => ({filters: {employer: frm.doc.employer, status: ["!=", "Cerrado"]}})},
            {fieldname: "client_number", fieldtype: "Data", label: __("Nro. Cliente")},
            {fieldtype: "Column Break"},
            {fieldname: "loan_number", fieldtype: "Data", label: __("Nro. Crédito")},
            {fieldname: "installment_number", fieldtype: "Data", label: __("Nro. Cuota")},
        ],
        primary_action_label: __("Crear, confirmar y agregar a destinos"),
        primary_action: async values => {
            if (busy) return;
            busy = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                const response = await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.create_complementary_item",
                    args: {remittance_name: frm.doc.name, modified: frm.doc.modified, values},
                    freeze: true, freeze_message: __("Creando partida y destino…"),
                });
                dialog.hide();
                await frm.reload_doc();
                frappe.msgprint({title: __("Partida agregada"), message: `${frappe.utils.escape_html(response.message.name)} · ${frappe.utils.escape_html(response.message.accounting_status)}.<br>Complete los destinos y use Conciliar.`});
            } finally {
                busy = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
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
                Los destinos vinculados a otra fila no se muestran. Para liberar un destino, seleccione su fila, desmárquelo y guarde.
                Al cambiar de fila se descartan las marcas sin guardar. Volver a importar el archivo elimina estos vínculos.</p>`},
            {fieldname: "destinations", fieldtype: "Table", label: __("Destinos del depósito"),
                cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true,
                fields: [
                    {fieldname: "linked", fieldtype: "Check", label: __("Vincular"), in_list_view: 1, columns: 1},
                    {fieldname: "destination", fieldtype: "Small Text", label: __("Destino"), read_only: 1, in_list_view: 1, columns: 7},
                    {fieldname: "amount_usd", fieldtype: "Currency", label: __("Asignado US$"), read_only: 1, in_list_view: 1, columns: 2},
                    {fieldname: "target_id", fieldtype: "Data", hidden: 1},
                ], data: []},
        ],
        primary_action_label: __("Guardar vínculos"),
        primary_action: async () => {
            if (busy) return;
            const rowName = labels.get(dialog.get_value("detail"));
            if (!rowName) return;
            const chosen = new Set((dialog.get_value("destinations") || []).filter(row => row.linked).map(row => row.target_id));
            const detail = rows.find(row => row.name === rowName);
            for (const target of targets) {
                if (target.detail_row === rowName || chosen.has(target.name)) {
                    target.detail_row = chosen.has(target.name) ? rowName : "";
                    target.detail_row_label = target.detail_row ? `Fila ${detail.source_row || detail.idx} · ${detail.client_name}` : "";
                }
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

frappe.ui.form.on("CN Remittance Target", {
    targets_add: renderRemittanceOverview,
    targets_remove: renderRemittanceOverview,
    amount_usd: renderRemittanceOverview,
    period: renderRemittanceOverview,
    row_key: renderRemittanceOverview,
    historical_application: renderRemittanceOverview,
    complementary_item: renderRemittanceOverview,
});

function downloadRemittanceTemplate(frm) {
    const params = new URLSearchParams({ template_type: "deposito" });
    if (frm.doc.detail_period) params.set("period_name", frm.doc.detail_period);
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

function remittancePresentation(doc, dirty = false) {
    const registration = doc.docstatus === 2 ? "Cancelado" : doc.docstatus === 1 ? "Confirmado" : "Borrador";
    const reconciled = doc.docstatus === 1 && doc.result === "Conciliado";
    const review = doc.docstatus === 1 && ["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(doc.result);
    let hint = "Depósito en borrador: todavía no participa en la conciliación. Complete los datos y guarde; después confirme o solicite confirmación a un supervisor.";
    if (doc.docstatus === 2) hint = "Este depósito está cancelado y no participa en la conciliación.";
    else if (doc.docstatus === 1) {
        if (dirty) hint = "Hay cambios sin guardar. Los resultados mostrados corresponden a la última conciliación; guarde y luego use Conciliar.";
        else if (!doc.detail_count && !(doc.targets || []).length && doc.result !== "Conciliado" && !review) {
            hint = "Depósito confirmado, pendiente de detalle por cliente o destinos manuales. Puede cargar el archivo cuando llegue, aunque sea en otro mes.";
        }
        else if (doc.result === "Pendiente" || doc.detail_status === "Cargado; pendiente de conciliación") {
            hint = "Hay información pendiente de conciliar. Use Conciliar para actualizar el resultado.";
        } else if (review && Number(doc.allocated_usd) > 0 && Number(doc.unallocated_usd) === 0) {
            hint = "El dinero está distribuido, pero la revisión no ha terminado. Revise los estados y motivos en Detalle por cliente y Destinos.";
        } else if (review) hint = "Revise los estados y motivos en Detalle por cliente y Destinos. Después guarde y use Conciliar.";
        else if (reconciled) hint = "Depósito conciliado. Consulte la distribución y las excepciones en Resultado.";
        else hint = "Revise el detalle o seleccione partidas en Destinos. Guarde y use Conciliar para actualizar los saldos.";
    }
    return {
        registration, hint,
        result: doc.docstatus === 2 ? "No participa" : doc.docstatus !== 1 ? "Sin conciliar" : doc.result || "Pendiente",
        color: doc.docstatus === 2 ? "gray" : dirty ? "orange" : reconciled ? "green" : review ? "orange" : "blue",
    };
}

function remittanceMoney(value) {
    const amount = Number(value || 0);
    return Number.isFinite(amount) ? amount.toLocaleString("es-NI", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}

function renderRemittanceOverview(frm) {
    if (!frm.dashboard || !frm.$wrapper) return;
    const escape = value => frappe.utils.escape_html(String(value || ""));
    const state = remittancePresentation(frm.doc, frm.is_dirty());
    const confirmed = frm.doc.docstatus === 1;
    const cards = [
        ["Equivalente del depósito", `US$ ${remittanceMoney(frm.doc.amount_usd)}`],
        ["Distribuido · última conciliación", confirmed ? `US$ ${remittanceMoney(frm.doc.allocated_usd)}` : "—"],
        ["Sin distribuir · última conciliación", confirmed ? `US$ ${remittanceMoney(frm.doc.unallocated_usd)}` : "—"],
        ["Estado del detalle", frm.doc.detail_status || "Sin detalle cargado"],
    ];
    const html = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <strong>${escape(frm.doc.employer || "Nuevo depósito")}</strong>
        <span>${escape(state.registration)} · <span class="indicator-pill ${state.color}">${escape(state.result)}</span></span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px">
        ${cards.map(([label, value]) => `<div style="padding:12px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg)">
            <div class="small text-muted">${escape(label)}</div><div style="font-size:16px;font-weight:600;margin-top:4px">${escape(value)}</div>
        </div>`).join("")}
    </div><p class="text-muted" style="margin:12px 0 0" role="status">${escape(state.hint)}</p>`;
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

async function loadPendingRemittanceTargets(frm) {
    const response = await frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.get_pending_targets",
        args: { remittance_name: frm.doc.name, targets: JSON.stringify(frm.doc.targets || []) },
        freeze: true,
        freeze_message: __("Consultando partidas pendientes…"),
    });
    return response.message;
}

class RemittanceTargetPicker {
    constructor(frm, data) {
        this.frm = frm;
        this.data = data;
        this.selected = new Map();
        this.page = 0;
        this.pageSize = 50;
        this.dialog = new frappe.ui.Dialog({
            title: __("Seleccionar partidas pendientes"), size: "extra-large",
            fields: [
                { fieldname: "intro", fieldtype: "HTML" },
                { fieldname: "search", fieldtype: "Data", label: __("Buscar cliente, crédito o referencia"),
                    onchange: () => this.filter() },
                { fieldtype: "Column Break" },
                { fieldname: "period", fieldtype: "Link", options: "CN Reconciliation Period",
                    label: __("Período (opcional)"), onchange: () => this.filter(),
                    get_query: () => ({ filters: { employer: data.employer, status: ["!=", "Cerrado"] } }) },
                { fieldtype: "Column Break" },
                { fieldname: "kind", fieldtype: "Select", label: __("Tipo de partida"),
                    options: ["Todos", "Cobranza", "Aplicación histórica", "Partida complementaria"],
                    onchange: () => this.filter() },
                { fieldtype: "Section Break" },
                { fieldname: "summary", fieldtype: "HTML" },
                { fieldname: "items", fieldtype: "HTML" },
            ],
            primary_action_label: __("Agregar destinos"),
            primary_action: () => this.apply(),
        });
        this.dialog.fields_dict.intro.$wrapper.html(`<p><strong>${this.escape(data.employer)}</strong> · US$</p>
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
        this.dialog.show();
        this.render();
    }

    escape(value) { return frappe.utils.escape_html(String(value || "")); }
    currency(cents) {
        return (cents / 100).toLocaleString("es-NI", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    total() { return [...this.selected.values()].reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0); }
    valid() {
        return this.selected.size > 0 && this.total() <= this.data.available_cents &&
            this.data.rows.every(row => !this.selected.has(row.id) || (
                Number.isSafeInteger(this.selected.get(row.id)) && this.selected.get(row.id) > 0 &&
                this.selected.get(row.id) <= row.pending_cents
            ));
    }
    select(row) {
        if (this.selected.has(row.id)) return;
        const amount = Math.min(row.pending_cents, Math.max(0, this.data.available_cents - this.total()));
        if (amount > 0) this.selected.set(row.id, amount);
    }
    filter() { this.page = 0; if (this.dialog && this.dialog.$wrapper.is(":visible")) this.render(); }
    summary() {
        const remaining = this.data.available_cents - this.total();
        const invalid = this.selected.size && !this.valid();
        this.dialog.fields_dict.summary.$wrapper.html(`<div class="alert ${invalid ? "alert-danger" : "alert-info"}" role="status" aria-live="polite">
            <div style="display:flex;flex-wrap:wrap;gap:12px 28px">
                <span>Disponible: <strong>US$ ${this.currency(this.data.available_cents)}</strong></span>
                <span>Seleccionado (${this.selected.size}): <strong>US$ ${this.currency(this.total())}</strong></span>
                <span>Restante: <strong>US$ ${this.currency(remaining)}</strong></span>
            </div>
            ${invalid ? "<div>Revise los importes: deben ser positivos, tener hasta dos decimales y no superar el pendiente ni el disponible.</div>" : ""}
            ${!this.data.available_cents ? "<div>El depósito ya está distribuido o reservado en sus destinos. Revise los destinos existentes.</div>" : ""}
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
        const filtered = this.data.rows.filter(row => {
            const text = normalize([row.client_name, row.client_number, row.national_id, row.employee_number,
                row.loan_number, row.reference, row.period_label].join(" "));
            return (!period || row.filter_period === period) && (!kind || kind === "Todos" || row.kind === kind)
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
                    ${selected ? "checked" : ""} ${!selected && this.total() >= this.data.available_cents ? "disabled" : ""}></td>
                <td><strong>${this.escape(row.client_name || "Sin nombre informado")}</strong>
                    <div class="small text-muted">${this.escape(identity)}</div>
                    <div>${row.loan_number ? `Crédito: ${this.escape(row.loan_number)}` : ""}</div></td>
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
                <th style="width:36px"><span class="sr-only">Seleccionar</span></th><th>Cliente / crédito</th><th>Origen / referencia</th>
                <th class="text-right text-nowrap" title="Importe aplicado en el core">Aplicado US$</th>
                <th class="text-right text-nowrap" title="Importe ya distribuido desde depósitos confirmados">Asignado US$</th>
                <th class="text-right text-nowrap" title="Saldo disponible para asignar; considera deducciones, partidas complementarias y ajustes de conciliación">Pendiente US$</th><th class="text-nowrap">Asignar US$</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="7" class="text-center text-muted">No hay partidas pendientes con estos filtros. Verifique las aplicaciones históricas o las deducciones de la empresa y ejecute Conciliar para actualizar los saldos.</td></tr>'}</tbody></table>
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
            const fresh = await loadPendingRemittanceTargets(this.frm);
            const byId = new Map(fresh.rows.map(row => [row.id, row]));
            if (fresh.modified !== this.data.modified || this.total() > fresh.available_cents ||
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
                    notes: [row.client_name, row.loan_number && `Crédito ${row.loan_number}`, row.period_label, row.reference].filter(Boolean).join(" · "),
                });
            }
            this.frm.refresh_field("targets");
            this.frm.dirty();
            renderRemittanceOverview(this.frm);
            this.dialog.hide();
            frappe.show_alert({ message: __("Destinos agregados. Guarde los cambios; la conciliación se ejecuta por separado."), indicator: "green" });
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
