window.credinominaShowRelatedPending = function (result) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const applications = result.applications || [];
    const rows = result.rows || [];
    const renderRows = (items, emptyMessage) => items.map(row => {
        const slug = row.type === "Aplicación" ? "cn-accounting-import"
            : row.type === "Depósito" ? "cn-remittance-allocation" : "cn-complementary-item";
        const href = `/app/${slug}/${encodeURIComponent(String(row.document || ""))}`;
        const deposits = row.type === "Aplicación" ? (row.deposits || []) : [];
        const depositLinks = deposits.length ? `<div style="margin-top:6px"><span class="text-muted">${esc(__("Depósitos asignados"))}:</span>
            ${deposits.map(deposit => {
                const title = [deposit.date, deposit.reference,
                    deposit.via_collection ? __("Vinculado mediante cobranza; consulte la distribución en el depósito.") : ""].filter(Boolean).join(" · ");
                return `<div><a href="/app/cn-remittance-allocation/${encodeURIComponent(String(deposit.document))}" target="_blank" rel="noopener noreferrer" title="${esc(title)}">${esc(deposit.document)}</a>${deposit.via_collection ? ` <span class="text-muted">(${esc(__("vía cobranza"))})</span>` : ""}</div>`;
            }).join("")}</div>` : "";
        const items = row.type === "Aplicación" ? (row.complementary_items || []) : [];
        const itemLinks = items.length ? `<div style="margin-top:6px"><span class="text-muted">${esc(__("Partidas complementarias vinculadas"))}:</span>
            ${items.map(item => `<div><a href="/app/cn-complementary-item/${encodeURIComponent(String(item.document))}" target="_blank" rel="noopener noreferrer" title="${esc([item.date, item.reference].filter(Boolean).join(" · "))}">${esc(item.document)}</a></div>`).join("")}</div>` : "";
        return `<tr>
            <td><a href="${href}" target="_blank" rel="noopener noreferrer">${esc(row.document)}</a>
                <div class="text-muted">${esc(__(row.type))}${row.source_row ? ` · ${esc(__("Fila"))} ${esc(row.source_row)}` : ""}</div>
                <div class="text-muted">${esc(row.date || "—")}${row.reference ? ` · ${esc(row.reference)}` : ""}</div>${depositLinks}${itemLinks}</td>
            <td>${esc(row.loan_number || "—")}</td>
            <td class="text-right" style="white-space:nowrap">${amount(row.amount_usd)}</td>
            <td class="text-right" style="white-space:nowrap">${amount(row.pending_usd)}</td>
            <td>${esc(__(row.status || "Pendiente"))}</td></tr>`;
    }).join("") || `<tr><td colspan="5">${esc(__(emptyMessage))}</td></tr>`;
    const panel = (title, items, emptyMessage) => `<section style="min-width:0">
        <h5 style="margin:0 0 10px">${esc(__(title))} (${items.length})</h5>
        <div class="table-responsive" style="max-height:60vh;overflow:auto"><table class="table table-bordered table-hover" style="font-size:12px;min-width:560px">
            <thead><tr>${["Documento / fecha / referencia", "Nro. Crédito", "Importe US$", "Pendiente US$", "Estado"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
            <tbody>${renderRows(items, emptyMessage)}</tbody>
        </table></div></section>`;
    const dialog = new frappe.ui.Dialog({
        title: __("Registros pendientes de {0}", [result.client_name || result.client_number || "—"]),
        size: "extra-large",
        fields: [{fieldname: "related_html", fieldtype: "HTML"},
            ...(result.on_complete_client ? [{fieldname: "complete_client_fields", fieldtype: "Button",
                label: __("Completar datos vacíos de la partida"), async click() {
                    if (dialog._completing_client) return;
                    dialog._completing_client = true;
                    const control = dialog.fields_dict.complete_client_fields;
                    control?.$input?.prop("disabled", true);
                    try {
                        if (await result.on_complete_client()) control?.$wrapper.hide();
                    } finally {
                        dialog._completing_client = false;
                        control?.$input?.prop("disabled", false);
                    }
                }}] : [])],
        ...(result.context || result.client ? {secondary_action_label: __("Abrir mesa de conciliación"), secondary_action() {
            dialog.hide();
            frappe.route_options = {cn_workbench: {employer: result.employer,
                ...(result.context ? {context: result.context} : {client: result.client})}};
            frappe.set_route("mesa-conciliacion");
        }} : {}),
        primary_action_label: __("Cerrar"), primary_action() { dialog.hide(); },
    });
    dialog.fields_dict.related_html.$wrapper.html(`<p class="text-muted">${esc(__("Registros pendientes de la misma empresa y cliente, aunque el crédito sea diferente. Verifique las coincidencias por nombre antes de conciliar. Solo se muestran documentos que tiene permiso de consultar."))}</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px;align-items:start">
            ${panel("Aplicaciones pendientes", applications, "No se encontraron aplicaciones pendientes para este cliente.")}
            ${panel("Depósitos y partidas complementarias pendientes", rows, "No se encontraron depósitos ni partidas complementarias pendientes para este cliente.")}
        </div><p class="text-muted small" style="margin-top:8px">${esc(__("«—» en el saldo de una aplicación indica que no se puede atribuir un pendiente individual con certeza; consulte su período."))}</p>`);
    dialog.show();
};
