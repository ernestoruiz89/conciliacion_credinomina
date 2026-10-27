frappe.query_reports["Revision de Partidas Manuales"] = {
    filters: [
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "item", label: __("Partida"), fieldtype: "Link", options: "CN Complementary Item"},
        {fieldname: "from_date", label: __("Desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Hasta"), fieldtype: "Date"},
        {fieldname: "include_tolerance", label: __("Incluir tolerancias históricas"), fieldtype: "Check", default: 1},
        {fieldname: "show_details", label: __("Mostrar detalle de vínculos"), fieldtype: "Check", default: 0},
    ],
    onload(report) {
        report.page.wrapper.off("click.manual-item-links", ".manual-item-links")
            .on("click.manual-item-links", ".manual-item-links", function(event) {
                event.preventDefault();
                return frappe.query_reports["Revision de Partidas Manuales"].open_details(this.dataset.item);
            });
    },
    async open_details(item) {
        const settings = this;
        let loading = false;
        const dialog = new frappe.ui.Dialog({
            title: __("Detalle de partida") + ": " + item,
            size: "extra-large",
            fields: [{fieldname: "detail", fieldtype: "HTML"}],
            primary_action_label: __("Actualizar"),
            primary_action: () => load(),
        });
        const body = dialog.fields_dict.detail.$wrapper;
        const load = async () => {
            if (loading) return;
            loading = true;
            dialog.disable_primary_action();
            body.html(`<p class="text-muted">${__("Cargando vínculos…")}</p>`);
            try {
                const response = await frappe.call({
                    method: "credinomina_reconciliation.manual_items_review.get_item_details",
                    args: {item},
                });
                body.html(settings.render_details(response.message || {}));
            } catch (error) {
                body.html(`<p class="text-danger">${__("No se pudo cargar el detalle. Pulse Actualizar para reintentar.")}</p>`);
            } finally {
                loading = false;
                dialog.enable_primary_action();
            }
        };
        dialog.show();
        await load();
    },
    detail_cell(value, column, row) {
        if (value === undefined || value === null || value === "") return "";
        const escape = frappe.utils.escape_html;
        if (column.fieldtype === "Link" || column.fieldtype === "Dynamic Link") {
            const doctype = column.fieldtype === "Link" ? column.options : row[column.options];
            if (doctype) {
                const url = frappe.utils.get_form_link(doctype, String(value));
                return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(String(value))}</a>`;
            }
        }
        if (["Currency", "Float", "Int", "Date"].includes(column.fieldtype)) {
            return escape(String(frappe.format(value, column, {}, row)));
        }
        return escape(String(value));
    },
    render_details(data) {
        if (!data.summary) return `<p class="text-muted">${__("No hay detalle visible para esta partida.")}</p>`;
        const escape = frappe.utils.escape_html;
        const summary = (data.summary_columns || []).filter(column => {
            const value = data.summary[column.fieldname];
            return value !== undefined && value !== null && value !== "";
        }).map(column => `<div class="col-md-4 mb-3"><div class="text-muted">${escape(column.label)}</div>
            <div style="white-space:pre-wrap;overflow-wrap:anywhere">${this.detail_cell(data.summary[column.fieldname], column, data.summary)}</div></div>`).join("");
        const columns = data.columns || [];
        const heading = columns.map(column => `<th style="min-width:160px">${escape(column.label)}</th>`).join("");
        const rows = (data.rows || []).map(row => `<tr>${columns.map(column =>
            `<td style="white-space:pre-wrap;overflow-wrap:anywhere">${this.detail_cell(row[column.fieldname], column, row)}</td>`).join("")}</tr>`).join("");
        return `<div class="row">${summary}</div><p class="text-muted">${escape(data.message || "")}</p>
            <div style="overflow:auto;max-height:55vh"><table class="table table-bordered table-sm">
                <thead style="position:sticky;top:0;background:var(--card-bg);z-index:1"><tr>${heading}</tr></thead>
                <tbody>${rows}</tbody></table></div>`;
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "details" && data?.item) {
            return `<a href="#" class="manual-item-links" data-item="${frappe.utils.escape_html(data.item)}">${__("Ver vínculos")}</a>`;
        }
        return default_formatter(value, row, column, data);
    },
};
