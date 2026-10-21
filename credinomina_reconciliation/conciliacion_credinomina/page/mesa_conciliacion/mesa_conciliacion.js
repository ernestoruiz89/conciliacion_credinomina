(function () {
    const API = "credinomina_reconciliation.reconciliation_workbench.";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const cash = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const date = value => value ? esc(frappe.datetime.str_to_user(value)) : "—";
    const fold = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    const href = row => `/app/${row.route}/${encodeURIComponent(row.document)}`;

    class ReconciliationWorkbench {
        constructor(wrapper) {
            this.page = frappe.ui.make_app_page({parent: wrapper, title: __("Mesa de conciliación"), single_column: true});
            this.sequence = 0;
            this.previewSequence = 0;
            this.context = null;
            this.data = null;
            this.selection = {application: "", source: ""};
            this.amount = "";
            this.pages = {applications: 0, rows: 0};
            this.ready = false;
            this.employer = this.page.add_field({fieldname: "employer", fieldtype: "Link", options: "CN Employer",
                label: __("Empresa"), change: () => this.changeScope(true)});
            this.client = this.page.add_field({fieldname: "client", fieldtype: "Link", options: "CN Client",
                label: __("Cliente"), get_query: () => ({filters: {employer: this.employer.get_value() || ""}}),
                change: () => this.changeScope(false)});
            this.page.set_primary_action(__("Consultar / actualizar"), () => this.load());
            this.$root = $('<div class="cn-workbench"></div>').appendTo(this.page.main);
            this.$root.html(`${styles()}<div class="cn-wb-intro"></div><div class="cn-wb-content"></div>`);
            this.$root.on("change", "[data-select]", event => {
                const input = event.currentTarget;
                this.selection[input.dataset.select] = input.value;
                this.amount = "";
                this.invalidatePreview();
                this.renderTables();
                this.renderSelection();
            });
            this.$root.on("input change", "[data-search], [data-kind]", () => {
                this.pages = {applications: 0, rows: 0};
                this.renderTables();
            });
            this.$root.on("input", "[data-amount]", event => {
                this.amount = event.currentTarget.value;
                this.invalidatePreview();
            });
            this.$root.on("click", "[data-check]", () => this.check());
            this.$root.on("click", "[data-continue]", () => this.continue());
            this.$root.on("click", "[data-page]", event => {
                const button = event.currentTarget;
                this.pages[button.dataset.side] += Number(button.dataset.page);
                this.renderTables();
            });
            this.$root.on("click", "[data-clear-context]", () => {
                this.context = null;
                this.changeScope(false);
            });
            this.ready = true;
            this.empty();
        }

        async show() {
            const options = frappe.route_options?.cn_workbench;
            if (!options) return this.data ? this.load() : undefined;
            delete frappe.route_options.cn_workbench;
            this.sequence++;
            this.invalidatePreview();
            this.ready = false;
            this.context = options.context || null;
            await this.employer.set_value(options.employer || "");
            await this.client.set_value(options.client || "");
            this.ready = true;
            return this.load();
        }

        changeScope(companyChanged) {
            if (!this.ready) return;
            this.sequence++;
            this.context = null;
            this.data = null;
            if (companyChanged) {
                this.ready = false;
                this.client.set_value("");
                this.ready = true;
            }
            this.selection = {application: "", source: ""};
            this.invalidatePreview();
            this.empty();
        }

        scope() {
            return {employer: this.employer.get_value() || null, client: this.client.get_value() || null,
                context: this.context ? JSON.stringify(this.context) : null};
        }

        empty(message) {
            this.$root.find(".cn-wb-intro").html(`<p>${esc(__("Consulte los pendientes de un cliente, seleccione una aplicación y un depósito o partida, y compruebe el vínculo antes de continuar."))}</p>`);
            this.$root.find(".cn-wb-content").html(`<div class="cn-wb-empty" role="status">${esc(message || __("Seleccione empresa y cliente y pulse Consultar / actualizar. También puede abrir esta mesa desde Registros pendientes."))}</div>`);
        }

        async load() {
            const args = this.scope();
            if (!args.context && (!args.employer || !args.client)) {
                this.empty();
                return;
            }
            const sequence = ++this.sequence;
            this.data = null;
            this.selection = {application: "", source: ""};
            this.amount = "";
            this.invalidatePreview();
            this.empty(__("Consultando movimientos pendientes…"));
            try {
                const {message} = await frappe.call({method: API + "get_data", args});
                if (sequence !== this.sequence) return;
                this.data = message;
                this.pages = {applications: 0, rows: 0};
                this.render();
            } catch (error) {
                if (sequence === this.sequence) this.empty(__("No se pudo consultar la mesa. Revise los filtros y vuelva a consultar."));
            }
        }

        render() {
            const data = this.data;
            this.$root.find(".cn-wb-intro").html(`<div class="cn-wb-heading"><div><h4>${esc(data.client_name)}</h4>
                <p class="text-muted">${esc(data.employer)}${data.client_number ? ` · ${esc(__("Cliente"))} ${esc(data.client_number)}` : ""}</p></div>
                ${this.context ? `<button class="btn btn-default btn-sm" data-clear-context>${esc(__("Elegir otro cliente"))}</button>` : ""}</div>
                <p>${esc(__("Los movimientos pueden corresponder a distintos créditos o meses. Los pendientes mostrados provienen de los registros guardados; la comprobación consulta la disponibilidad actual."))}</p>`);
            this.$root.find(".cn-wb-content").html(`<div class="cn-wb-filters">
                <label>${esc(__("Buscar crédito, período, referencia o documento"))}<input type="search" class="form-control" data-search></label>
                <label>${esc(__("Origen"))}<select class="form-control" data-kind><option value="">${esc(__("Depósitos y partidas"))}</option><option value="Depósito">${esc(__("Depósitos"))}</option><option value="Partida complementaria">${esc(__("Partidas complementarias"))}</option></select></label></div>
                <div class="cn-wb-columns"><section><h5>${esc(__("1. Aplicaciones pendientes"))}</h5><div data-table="applications"></div></section>
                <section><h5>${esc(__("2. Depósitos y partidas pendientes"))}</h5><div data-table="rows"></div></section></div>
                <p class="small text-muted">${esc(__("«—» indica un saldo individual que todavía no puede determinarse. Los saldos negativos requieren revisar la distribución. Abrir un documento permite consultar su evidencia."))}</p>
                <section class="cn-wb-check"><h5>${esc(__("3. Comprobación previa"))}</h5><div data-selection></div>
                <div data-preview aria-live="polite"></div></section>`);
            this.renderTables();
            this.renderSelection();
        }

        renderTables() {
            if (!this.data) return;
            const search = fold(this.$root.find("[data-search]").val());
            const kind = this.$root.find("[data-kind]").val();
            for (const side of ["applications", "rows"]) {
                const key = side === "applications" ? "application" : "source";
                const rows = this.data[side].filter(row => (side === "applications" || !kind || row.type === kind) &&
                    fold([row.document, row.loan_number, row.reference, row.period, row.date, row.status, row.difference_reason].join(" ")).includes(search));
                const pageCount = Math.max(1, Math.ceil(rows.length / 50));
                this.pages[side] = Math.min(Math.max(0, this.pages[side]), pageCount - 1);
                const offset = this.pages[side] * 50;
                this.$root.find(`[data-table="${side}"]`).html(`<div class="cn-wb-scroll"><table class="table table-bordered table-hover"><thead><tr>
                    <th><span class="sr-only">${esc(__("Seleccionar"))}</span></th><th>${esc(__("Documento / período / crédito"))}</th>
                    <th class="text-right">${esc(__("Importe US$"))}</th><th class="text-right">${esc(__("Pendiente US$"))}</th><th>${esc(__("Estado"))}</th></tr></thead>
                    <tbody>${rows.slice(offset, offset + 50).map(row => `<tr class="${this.selection[key] === row.id ? "cn-wb-selected" : ""}">
                        <td><input type="radio" name="cn-wb-${key}" data-select="${key}" value="${esc(row.id)}" ${this.selection[key] === row.id ? "checked" : ""} aria-label="${esc(__("Seleccionar {0}", [row.document]))}"></td>
                        <td><a href="${href(row)}" target="_blank" rel="noopener noreferrer">${esc(row.document)}</a><div>${esc(__(row.type))}${row.source_row ? ` · ${esc(__("Fila"))} ${esc(row.source_row)}` : ""} · ${date(row.date)}</div>
                            <div>${esc(__("Crédito"))}: <strong>${esc(row.loan_number || "—")}</strong></div>${row.period ? `<div class="text-muted">${esc(row.period)}</div>` : ""}
                            ${row.reference ? `<div class="text-muted">${esc(__("Ref."))} ${esc(row.reference)}</div>` : ""}</td>
                        <td class="cn-wb-money">${cash(row.amount_usd)}</td><td class="cn-wb-money">${cash(row.pending_usd)}</td>
                        <td>${esc(__(row.status || "Pendiente"))}${row.docstatus === 0 ? `<div class="text-muted">${esc(__("Borrador"))}</div>` : ""}${row.direction ? `<div>${esc(__(row.direction))}</div>` : ""}${row.difference_reason ? `<div class="text-muted">${esc(row.difference_reason)}</div>` : ""}</td></tr>`).join("") || `<tr><td colspan="5">${esc(__("No hay pendientes con estos filtros."))}</td></tr>`}</tbody></table></div>
                    <div class="cn-wb-pagination"><span>${rows.length ? offset + 1 : 0}–${Math.min(offset + 50, rows.length)} ${esc(__("de"))} ${rows.length}</span>
                    <div><button class="btn btn-default btn-xs" data-side="${side}" data-page="-1" ${this.pages[side] === 0 ? "disabled" : ""}>${esc(__("Anterior"))}</button>
                    <button class="btn btn-default btn-xs" data-side="${side}" data-page="1" ${this.pages[side] + 1 >= pageCount ? "disabled" : ""}>${esc(__("Siguiente"))}</button></div></div>`);
            }
        }

        selected() {
            return {application: this.data?.applications.find(row => row.id === this.selection.application),
                source: this.data?.rows.find(row => row.id === this.selection.source)};
        }

        renderSelection() {
            const {application, source} = this.selected();
            const label = row => row ? `${esc(row.document)} · ${esc(__("Crédito"))} ${esc(row.loan_number || "—")}` : esc(__("Sin seleccionar"));
            this.$root.find("[data-selection]").html(`<div class="cn-wb-choice"><div><strong>${esc(__("Aplicación"))}</strong><div>${label(application)}</div></div>
                <div><strong>${esc(__("Depósito o partida"))}</strong><div>${label(source)}</div></div></div>
                <div class="cn-wb-amount"><label>${esc(__("Importe a vincular US$ (opcional)"))}<input class="form-control" type="number" min="0.01" step="0.01" data-amount value="${esc(this.amount)}" placeholder="${esc(__("Sugerir importe disponible"))}"></label>
                <button class="btn btn-primary" data-check ${application && source ? "" : "disabled"}>${esc(__("Comprobar selección"))}</button></div>
                <p class="small text-muted">${esc(__("La comprobación no guarda asignaciones. Después podrá continuar en el depósito o la partida para completar la operación."))}</p>`);
        }

        invalidatePreview() {
            this.previewSequence++;
            this.preview = null;
            this.$root?.find("[data-preview]").empty();
            const {application, source} = this.selected();
            this.$root?.find("[data-check]").prop("disabled", !(application && source));
        }

        async check() {
            const {application, source} = this.selected();
            if (!application || !source) return null;
            const sequence = ++this.previewSequence;
            this.preview = null;
            this.$root.find("[data-preview]").html(`<p role="status">${esc(__("Comprobando saldos, períodos y condiciones del vínculo…"))}</p>`);
            this.$root.find("[data-check]").prop("disabled", true);
            try {
                const {message} = await frappe.call({method: API + "preview", args: {...this.scope(),
                    application_id: application.id, source_id: source.id, amount: this.amount || null}});
                if (sequence !== this.previewSequence) return null;
                this.preview = message;
                this.renderPreview(message);
                return message;
            } catch (error) {
                if (sequence === this.previewSequence) this.$root.find("[data-preview]").html(`<p class="text-danger">${esc(__("No se pudo completar la comprobación. Actualice la mesa y vuelva a intentarlo."))}</p>`);
                return null;
            } finally {
                if (sequence === this.previewSequence) this.$root.find("[data-check]").prop("disabled", false);
            }
        }

        renderPreview(result) {
            const list = values => values.map(value => `<li>${esc(value)}</li>`).join("");
            this.$root.find("[data-preview]").html(`<div class="alert ${result.can_continue ? "alert-success" : "alert-warning"}"><strong>${esc(__(result.can_continue ? "Selección compatible para continuar" : "Hay condiciones por resolver"))}</strong>
                ${result.errors?.length ? `<ul>${list(result.errors)}</ul>` : ""}</div>
                ${result.warnings?.length ? `<div class="alert alert-info"><strong>${esc(__("Revise el respaldo"))}</strong><ul>${list(result.warnings)}</ul></div>` : ""}
                <div class="cn-wb-figures">${[
                    ["Aplicación disponible US$", result.application_pending_usd], ["Origen disponible US$", result.source_available_usd],
                    ["Importe propuesto US$", result.proposed_usd], ["Restaría en la aplicación US$", result.application_remaining_usd],
                    ["Restaría en el origen US$", result.source_remaining_usd],
                ].map(([label, value]) => `<div><span>${esc(__(label))}</span><strong>${cash(value)}</strong></div>`).join("")}</div>
                ${result.next_step ? `<p>${esc(result.next_step)}</p>` : ""}
                ${result.can_continue ? `<button class="btn btn-primary" data-continue>${esc(__(result.source?.type === "Depósito" ? "Continuar en el depósito" : "Continuar en la partida"))}</button>` : ""}
                <p class="small text-muted" style="margin-top:12px">${esc(__("Comprobado"))}: ${esc(result.checked_on || "")} · ${esc(__("Las cifras se vuelven a comprobar al continuar. La asignación se guarda y concilia desde el documento correspondiente."))}</p>`);
        }

        async continue() {
            this.$root.find("[data-continue]").prop("disabled", true);
            const result = await this.check();
            if (!result?.can_continue || frappe.get_route()[0] !== "mesa-conciliacion") return;
            window.credinominaWorkbenchSelection = {doctype: result.source.doctype, name: result.source.document,
                args: {...this.scope(), application_id: result.application.id, source_id: result.source.id,
                    amount: result.proposed_usd}};
            frappe.set_route("Form", result.source.doctype, result.source.document);
        }
    }

    // Keep the proposal in this browser session. Opening the form never saves it.
    window.credinominaWorkbenchHandoff = function (frm, openSelection) {
        const selection = window.credinominaWorkbenchSelection;
        if (!selection || selection.doctype !== frm.doc.doctype || selection.name !== frm.doc.name || !frm.get_perm(0, "write")) return;
        frm.add_custom_button(__("Continuar selección de la mesa"), async () => {
            if (selection.busy) return;
            if (frm.is_dirty()) return frappe.msgprint(__("Guarde sus cambios antes de continuar con la selección de la mesa."));
            selection.busy = true;
            try {
                const current = () => frm.doc.name === selection.name && frm.doc.doctype === selection.doctype &&
                    window.credinominaWorkbenchSelection === selection &&
                    frappe.get_route().join("/") === ["Form", selection.doctype, selection.name].join("/");
                const {message} = await frappe.call({method: API + "preview", args: selection.args,
                    freeze: true, freeze_message: __("Comprobando nuevamente la selección…")});
                if (!current()) return;
                if (!message?.can_continue) return frappe.msgprint({title: __("Actualice la selección de la mesa"),
                    message: (message?.errors || []).map(esc).join("<br>"), indicator: "orange"});
                await frm.reload_doc();
                if (current()) await openSelection(message);
            } finally { selection.busy = false; }
        }, __("Mesa de conciliación"));
    };

    function styles() {
        return `<style>.cn-workbench{padding:20px 0}.cn-wb-heading,.cn-wb-pagination{display:flex;justify-content:space-between;gap:12px;align-items:center}.cn-wb-heading h4{margin:0 0 6px}.cn-wb-empty{padding:40px 20px;text-align:center;background:var(--control-bg);border-radius:8px}.cn-wb-filters{display:flex;gap:16px;margin:18px 0}.cn-wb-filters label:first-child{flex:1}.cn-wb-filters label:last-child{min-width:220px}.cn-wb-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cn-wb-columns section{min-width:0}.cn-wb-scroll{overflow:auto;max-height:58vh;border:1px solid var(--border-color);border-radius:6px}.cn-wb-scroll table{margin:0;font-size:12px;min-width:570px}.cn-wb-scroll thead{position:sticky;top:0;background:var(--card-bg);z-index:1}.cn-wb-scroll td{vertical-align:top}.cn-wb-money{text-align:right;white-space:nowrap}.cn-wb-selected{background:var(--control-bg)}.cn-wb-pagination{font-size:12px;margin:8px 0}.cn-wb-check{border:1px solid var(--border-color);padding:18px;border-radius:8px;margin-top:22px}.cn-wb-choice{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}.cn-wb-amount{display:flex;align-items:flex-end;gap:14px;margin-bottom:12px}.cn-wb-amount label{margin:0;max-width:320px}.cn-wb-figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}.cn-wb-figures>div{background:var(--control-bg);border-radius:6px;padding:12px}.cn-wb-figures strong{display:block;font-size:17px;margin-top:6px}.cn-wb-figures span{font-size:12px}.cn-wb-check ul{margin:8px 0 0;padding-left:20px}@media(max-width:1000px){.cn-wb-columns{grid-template-columns:1fr}.cn-wb-scroll{max-height:45vh}}@media(max-width:600px){.cn-wb-filters,.cn-wb-amount{display:block}.cn-wb-choice{grid-template-columns:1fr}.cn-wb-amount button{margin-top:10px}}</style>`;
    }

    frappe.pages["mesa-conciliacion"].on_page_load = wrapper => { wrapper.cn_workbench = new ReconciliationWorkbench(wrapper); };
    frappe.pages["mesa-conciliacion"].on_page_show = wrapper => wrapper.cn_workbench?.show();
})();
