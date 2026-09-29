frappe.ui.form.on("CN Remittance Allocation", {
    setup(frm) {
        frm.set_query("bank_account", () => ({ filters: { active: 1 } }));
    },
    refresh(frm) {
        showDepositStage(frm);
        frm.toggle_display("select_pending_targets", frm.doc.docstatus !== 2 && !!frm.get_perm(0, "write"));
        frm.add_custom_button(__("Plantilla de detalle del depósito"), () => {
            const params = new URLSearchParams({ template_type: "deposito" });
            if (frm.doc.detail_period) params.set("period_name", frm.doc.detail_period);
            window.open(
                `/api/method/credinomina_reconciliation.template_download.download_import_template?${params}`,
                "_blank"
            );
        }, __("Plantillas"));
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
        frm.add_custom_button(__("Cargar detalle del depósito"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.import_remittance_detail",
                args: { remittance_name: frm.doc.name },
                freeze: true,
                callback: () => frm.reload_doc(),
            });
        });
    },
    deposit_amount: updateUsdEquivalent,
    deposit_currency: updateUsdEquivalent,
    fx_rate: updateUsdEquivalent,
    deposit_date: updateUsdEquivalent,
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
            <small>Se descuentan las asignaciones ya conciliadas y los destinos existentes, sin contarlos dos veces.</small>
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
                <th class="text-right">Pendiente US$</th><th>Asignar US$</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="5" class="text-center text-muted">No hay partidas pendientes con estos filtros. Verifique las aplicaciones históricas o las deducciones de la empresa y ejecute Conciliar para actualizar los saldos.</td></tr>'}</tbody></table>
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
            this.dialog.hide();
            frappe.show_alert({ message: __("Destinos agregados. Guarde los cambios; la conciliación se ejecuta por separado."), indicator: "green" });
        } finally {
            this.applying = false;
        }
    }
}

function showDepositStage(frm) {
    if (frm.is_new()) {
        frm.set_intro(__("Guarde el depósito y confírmelo cuando sus datos estén completos. Confirmar no ejecuta la conciliación; podrá conciliar por separado."), "blue");
    } else if (frm.doc.docstatus === 0) {
        frm.set_intro(
            frm.get_perm(0, "submit")
                ? __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; use Confirmar depósito y luego Conciliar.")
                : __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; solicite a un supervisor que confirme el depósito."),
            "orange"
        );
    } else if (frm.doc.docstatus === 2) {
        frm.set_intro(__("Depósito cancelado: no participa en la conciliación."), "red");
    } else if (frm.doc.detail_status === "Cargado; pendiente de conciliación") {
        frm.set_intro(__("Depósito confirmado; el detalle está cargado y pendiente de conciliación. Use Conciliar cuando quiera actualizar el resultado."), "orange");
    } else if (frm.doc.result === "Pendiente") {
        frm.set_intro(__("Depósito confirmado, pendiente de conciliación. Use Conciliar para calcular la distribución y el resultado."), "orange");
    } else if (!frm.doc.detail_count && !(frm.doc.targets || []).length) {
        frm.set_intro(__("Depósito confirmado, pendiente de detalle por cliente o distribución manual documentada. La conciliación se ejecuta por separado."), "orange");
    } else if (["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(frm.doc.result)) {
        frm.set_intro(__("Depósito confirmado, pero hay detalle o destinos pendientes de revisión."), "orange");
    } else if (frm.doc.result === "Conciliado") {
        frm.set_intro(__("Depósito confirmado y conciliado."), "green");
    } else {
        frm.set_intro(__("Depósito confirmado. Revise el resultado y el saldo sin distribuir."), "blue");
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
