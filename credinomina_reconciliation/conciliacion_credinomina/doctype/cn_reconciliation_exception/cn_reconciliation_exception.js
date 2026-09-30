frappe.ui.form.on("CN Reconciliation Exception", {
    setup(frm) {
        frm.set_query("period", () => ({filters: {
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {}),
            status: ["!=", "Cerrado"],
        }}));
        frm.set_query("source_import", () => ({filters: frm.doc.employer ? {employer: frm.doc.employer} : {}}));
    },
    refresh(frm) {
        frm.toggle_display("select_related_case", !frm.doc.exception_key && frm.doc.docstatus !== 2);
        cn_exception_lock_relation(frm);
        if (frm.doc.exception_key) {
            frm.set_intro(__("Excepción generada por conciliación. Su vínculo de origen se conserva; puede registrar la causa, el seguimiento y la resolución."), "blue");
        }
    },
    select_related_case(frm) {
        cn_exception_open_case_picker(frm);
    },
});

function cn_exception_open_case_picker(frm) {
    const api = "credinomina_reconciliation.exception_selection.";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    let rows = [], selected = null, start = 0, has_more = false, request = 0;
    let dialog;
    dialog = new frappe.ui.Dialog({
        title: __("Seleccionar caso relacionado"), size: "extra-large",
        fields: [
            {fieldname: "employer", fieldtype: "Link", options: "CN Employer", label: __("Empresa"),
                reqd: 1, default: frm.doc.employer, onchange: () => invalidate()},
            {fieldname: "kind", fieldtype: "Select", label: __("Buscar en"), options: "Cobranza\nAplicación",
                default: frm.doc.related_case_type || (frm.doc.source_import ? "Aplicación" : "Cobranza"),
                onchange: () => invalidate()},
            {fieldtype: "Column Break"},
            {fieldname: "period", fieldtype: "Link", options: "CN Reconciliation Period", label: __("Período (opcional)"),
                default: frm.doc.period, get_query: () => ({filters: {
                    employer: dialog.get_value("employer"), status: ["!=", "Cerrado"],
                }}), onchange: () => invalidate()},
            {fieldname: "search", fieldtype: "Data", label: __("Cliente, crédito, asiento o recibo"),
                description: __("En cobranzas puede buscar por nombre, número de cliente o crédito."),
                onchange: () => invalidate()},
            {fieldname: "find", fieldtype: "Button", label: __("Buscar casos"), click: () => load(0)},
            {fieldtype: "Section Break"},
            {fieldname: "results", fieldtype: "HTML"},
        ],
        primary_action_label: __("Vincular caso"),
        primary_action: async () => {
            if (!selected) return;
            const picked = selected;
            const apply = async () => {
                dialog.get_primary_btn().prop("disabled", true);
                try {
                    const response = await frappe.call({method: api + "resolve_related_case", args: {
                        employer: picked.employer, kind: picked.related_case_type,
                        row_id: picked.related_case_id, period: picked.period,
                    }, freeze: true, freeze_message: __("Verificando el caso seleccionado…")});
                    await frm.set_value(response.message);
                    cn_exception_lock_relation(frm);
                    dialog.hide();
                    frappe.show_alert({message: __("Caso vinculado. Revise el monto de la excepción y guarde el documento."), indicator: "green"});
                } finally {
                    dialog.get_primary_btn().prop("disabled", !selected);
                }
            };
            if (frm.doc.collection_row_id || frm.doc.source_import || frm.doc.related_case_id) {
                frappe.confirm(__("¿Reemplazar la relación actual? Se conservarán el monto, la descripción y las gestiones de la excepción."), apply);
            } else {
                await apply();
            }
        },
    });
    function invalidate() {
        // onchanges may fire while Frappe is constructing the dialog.
        if (!dialog) return;
        request += 1;
        selected = null;
        rows = [];
        dialog.get_primary_btn().prop("disabled", true);
        dialog.fields_dict.results.$wrapper.html(`<p class="text-muted">${esc(__("Pulse Buscar casos para actualizar los resultados."))}</p>`);
    }
    async function load(offset) {
        const employer = dialog.get_value("employer");
        if (!employer) {
            frappe.msgprint(__("Seleccione una empresa para buscar sus casos."));
            return;
        }
        const token = ++request;
        selected = null;
        rows = [];
        dialog.get_primary_btn().prop("disabled", true);
        dialog.fields_dict.results.$wrapper.html(`<p class="text-muted">${esc(__("Buscando casos…"))}</p>`);
        try {
            const response = await frappe.call({method: api + "get_related_cases", args: {
                employer, kind: dialog.get_value("kind"), period: dialog.get_value("period"),
                search: dialog.get_value("search"), start: offset,
            }});
            if (token !== request) return;
            rows = response.message.rows;
            start = response.message.start;
            has_more = response.message.has_more;
            render();
        } catch (error) {
            if (token === request) dialog.fields_dict.results.$wrapper.html(
                `<p class="text-danger">${esc(__("No fue posible cargar los casos. Revise los filtros y vuelva a buscar."))}</p>`
            );
        }
    }
    function render() {
        const wrapper = dialog.fields_dict.results.$wrapper;
        const date = value => value ? esc(frappe.datetime.str_to_user(String(value).slice(0, 10))) : "—";
        const amount = value => value == null ? esc(__("Sin tasa")) : esc(format_currency(value, "USD", 2));
        wrapper.html(`
            <p class="text-muted">${esc(__("Seleccione una fila. El importe mostrado es el total de la cobranza o aplicación, no el monto de la excepción."))}</p>
            <div class="table-responsive" style="max-height:380px;overflow:auto">
              <table class="table table-bordered table-hover">
                <thead><tr>${["", "Cliente / crédito", "Período / fecha", "Importe US$", "Estado / referencia"].map(h => `<th>${esc(__(h))}</th>`).join("")}</tr></thead>
                <tbody>${rows.map((row, i) => `<tr data-case-index="${i}" style="cursor:pointer">
                    <td><input type="radio" name="cn-related-case" value="${i}" aria-label="${esc(__("Seleccionar caso") + ": " + (row.client_name || row.client_number))}"></td>
                    <td><strong>${esc(row.client_name || __("Sin nombre"))}</strong><br>${esc(__("Cliente"))}: ${esc(row.client_number || "—")} · ${esc(__("Crédito"))}: ${esc(row.loan_number || "—")}</td>
                    <td>${esc(row.period || __("Sin período"))}<br><small>${date(row.date)}</small></td>
                    <td class="text-right text-nowrap">${amount(row.amount_usd)}</td>
                    <td>${esc(row.status)}<br><small>${esc(row.reference || "—")}</small></td>
                </tr>`).join("") || `<tr><td colspan="5" class="text-muted">${esc(__("No se encontraron casos disponibles. Revise el período, cambie entre cobranza y aplicación o amplíe la búsqueda."))}</td></tr>`}</tbody>
              </table>
            </div>
            <div class="flex justify-between align-center">
                <span class="text-muted">${esc(rows.length ? `${start + 1}–${start + rows.length}` : "0")} ${esc(__("casos mostrados"))}</span>
                <div><button type="button" class="btn btn-default btn-sm" data-page="previous" ${start ? "" : "disabled"}>${esc(__("Anterior"))}</button>
                <button type="button" class="btn btn-default btn-sm" data-page="next" ${has_more ? "" : "disabled"}>${esc(__("Siguiente"))}</button></div>
            </div>`);
        wrapper.find("tr[data-case-index]").on("click", function () {
            const index = Number($(this).attr("data-case-index"));
            selected = rows[index];
            wrapper.find("tr[data-case-index]").removeClass("table-active");
            $(this).addClass("table-active").find("input").prop("checked", true);
            dialog.get_primary_btn().prop("disabled", false);
        });
        wrapper.find("input[type=radio]").on("change", function () { $(this).closest("tr").trigger("click"); });
        wrapper.find('[data-page="previous"]').on("click", () => load(Math.max(0, start - 30)));
        wrapper.find('[data-page="next"]').on("click", () => load(start + 30));
    }
    dialog.show();
    invalidate();
    if (frm.doc.employer) load(0);
}

function cn_exception_lock_relation(frm) {
    for (const field of ["employer", "period", "source_import", "source_row", "client_number", "loan_number"]) {
        frm.set_df_property(field, "read_only", Boolean(frm.doc.related_case_id || frm.doc.exception_key));
    }
}
