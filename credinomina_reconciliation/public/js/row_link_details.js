(function () {
    if (frappe._cnRowLinksRegistered) return;
    frappe._cnRowLinksRegistered = true;
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const routes = {"CN Accounting Import": "cn-accounting-import", "CN Reconciliation Period": "cn-reconciliation-period",
        "CN Remittance Allocation": "cn-remittance-allocation", "CN Complementary Item": "cn-complementary-item"};

    async function render(frm, parentfield, rowName) {
        const gridRow = frm.fields_dict[parentfield]?.grid?.grid_rows_by_docname?.[rowName];
        const control = gridRow?.grid_form?.fields_dict?.linked_documents_html;
        if (!control) return;
        const wrapper = control.$wrapper;
        const sequence = control._cnLinksSequence = (control._cnLinksSequence || 0) + 1;
        const row = (frm.doc[parentfield] || []).find(row => row.name === rowName);
        wrapper.off(".cnRowLinks");
        wrapper.on("click.cnRowLinks", "[data-refresh-links]", () => render(frm, parentfield, rowName));
        if (!row || frm.is_new() || frm.is_dirty()) {
            wrapper.html(`<p class="text-muted">${esc(__("Guarde o recargue el documento para consultar los vínculos guardados de esta fila."))}</p>
                <button type="button" class="btn btn-default btn-sm" data-refresh-links>${esc(__("Actualizar vínculos"))}</button>`);
            return;
        }
        const name = frm.doc.name, modified = frm.doc.modified;
        const isCurrent = () => frm.doc.name === name && frm.doc.modified === modified && !frm.is_dirty()
            && control._cnLinksSequence === sequence && (frm.doc[parentfield] || []).includes(row)
            && frm.fields_dict[parentfield]?.grid?.grid_rows_by_docname?.[rowName]?.grid_form?.fields_dict?.linked_documents_html === control;
        wrapper.html(`<p class="text-muted">${esc(__("Consultando documentos vinculados…"))}</p>`);
        try {
            const response = await frappe.call({method: "credinomina_reconciliation.row_link_details.get_links", silent: true,
                args: {doctype: frm.doc.doctype, name, row_name: rowName, modified}});
            if (!isCurrent()) return;
            const data = response.message || {};
            if (String(data.modified) !== String(modified)) throw new Error("Stale links");
            const rows = data.rows || [];
            wrapper.html(`<div class="d-flex justify-content-between align-items-center mb-2"><strong>${esc(__("Documentos vinculados"))}</strong>
                <button type="button" class="btn btn-default btn-sm" data-refresh-links>${esc(__("Actualizar vínculos"))}</button></div>
                <div class="table-responsive"><table class="table table-bordered"><thead><tr>
                ${["Documento", "Tipo de vínculo", "Estado", "Importe US$"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
                <tbody>${rows.map(row => {
                    const route = routes[row.doctype];
                    const link = route && row.document ? `<a href="/app/${route}/${encodeURIComponent(row.document)}" target="_blank" rel="noopener noreferrer">${esc(row.document)}</a>` : esc(__("Documento no disponible"));
                    return `<tr><td>${link}${row.row_label ? `<div class="text-muted small">${esc(row.row_label)}</div>` : ""}</td>
                        <td>${esc(__(row.kind || ""))}</td><td>${esc(__(row.state || ""))}</td><td class="text-right">${amount(row.amount_usd)}</td></tr>`;
                }).join("") || `<tr><td colspan="4">${esc(__("Esta fila no tiene documentos vinculados guardados."))}</td></tr>`}</tbody>
                <tfoot><tr><th colspan="3">${esc(__("Total vinculado US$"))}</th><th class="text-right">${amount(data.total_usd)}</th></tr></tfoot></table></div>
                ${(data.notes || []).map(note => `<p class="text-muted small">${esc(__(note))}</p>`).join("")}`);
        } catch (_error) {
            if (!isCurrent()) return;
            wrapper.html(`<p class="text-danger">${esc(__("No se pudieron consultar los vínculos. Revise sus permisos o recargue el formulario e intente nuevamente."))}</p>
                <button type="button" class="btn btn-default btn-sm" data-refresh-links>${esc(__("Reintentar"))}</button>`);
        }
    }

    for (const [doctype, parentfield] of [["CN Remittance Detail", "detail_rows"], ["CN Source Row", "rows"]]) {
        frappe.ui.form.on(doctype, {form_render(frm, _cdt, cdn) {return render(frm, parentfield, cdn);}});
    }
})();
