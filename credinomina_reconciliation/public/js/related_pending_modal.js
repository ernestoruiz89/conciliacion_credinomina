window.credinominaShowRelatedPending = function (result) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const displayDate = value => {
        const text = String(value || "");
        const parts = /^(\d{4})-(\d{2})-(\d{2})(?:$|[ T])/.exec(text);
        return parts ? `${parts[3]}/${parts[2]}/${parts[1]}` : text;
    };
    const applications = result.applications || [];
    const rows = result.rows || [];
    const pendingTotalCents = (items, sign = () => 1) => {
        let cents = 0;
        for (const row of items) {
            const value = row.pending_usd;
            if (value == null || !["number", "string"].includes(typeof value) ||
                    String(value).trim() === "" || !Number.isFinite(Number(value)) || Number(value) < 0) return null;
            const part = Math.round(Number(value) * 100);
            const direction = part ? sign(row) : 1;
            if (direction == null || !Number.isSafeInteger(part)) return null;
            cents += part * direction;
            if (!Number.isSafeInteger(cents)) return null;
        }
        return cents;
    };
    const sourceSign = row => row.type === "Depósito" ? 1 : row.type === "Partida complementaria"
        ? row.direction === "Crédito" ? 1 : row.direction === "Débito" ? -1 : null : null;
    const applicationTotal = pendingTotalCents(applications);
    const sourceTotal = pendingTotalCents(rows, sourceSign);
    const differenceCents = applicationTotal == null || sourceTotal == null ? null : applicationTotal - sourceTotal;
    const difference = Number.isSafeInteger(differenceCents) ? differenceCents / 100 : null;
    const differenceClass = difference == null ? "alert-info" : difference > 0 ? "alert-warning"
        : difference < 0 ? "alert-info" : "alert-success";
    const differenceExplanation = difference == null
        ? __("La diferencia no se puede determinar: hay importes o sentidos contables pendientes de revisar.")
        : difference > 0 ? __("Las aplicaciones pendientes superan el total neto mostrado.")
        : difference < 0 ? __("El total neto mostrado supera las aplicaciones pendientes.") : __("Los totales coinciden.");
    const renderRows = (items, emptyMessage) => items.map(row => {
        const slug = row.type === "Aplicación" ? "cn-accounting-import"
            : row.type === "Depósito" ? "cn-remittance-allocation" : "cn-complementary-item";
        const href = `/app/${slug}/${encodeURIComponent(String(row.document || ""))}`;
        const direction = row.type === "Partida complementaria" && row.direction
            ? ` · <strong>${esc(__(row.direction))}</strong>` : "";
        const deposits = row.type === "Aplicación" ? (row.deposits || []) : [];
        const depositLinks = deposits.length ? `<div style="margin-top:6px"><span class="text-muted">${esc(__("Depósitos asignados"))}:</span>
            ${deposits.map(deposit => {
                const title = [displayDate(deposit.date), deposit.reference,
                    deposit.via_collection ? __("Vinculado mediante cobranza; consulte la distribución en el depósito.") : ""].filter(Boolean).join(" · ");
                return `<div><a href="/app/cn-remittance-allocation/${encodeURIComponent(String(deposit.document))}" target="_blank" rel="noopener noreferrer" title="${esc(title)}">${esc(deposit.document)}</a>${deposit.via_collection ? ` <span class="text-muted">(${esc(__("vía cobranza"))})</span>` : ""}</div>`;
            }).join("")}</div>` : "";
        const items = row.type === "Aplicación" ? (row.complementary_items || []) : [];
        const itemLinks = items.length ? `<div style="margin-top:6px"><span class="text-muted">${esc(__("Partidas complementarias vinculadas"))}:</span>
            ${items.map(item => `<div><a href="/app/cn-complementary-item/${encodeURIComponent(String(item.document))}" target="_blank" rel="noopener noreferrer" title="${esc([displayDate(item.date), item.reference].filter(Boolean).join(" · "))}">${esc(item.document)}</a></div>`).join("")}</div>` : "";
        return `<tr>
            <td><a href="${href}" target="_blank" rel="noopener noreferrer">${esc(row.document)}</a>
                <div class="text-muted">${esc(__(row.type))}${direction}${row.source_row ? ` · ${esc(__("Fila"))} ${esc(row.source_row)}` : ""}</div>
                <div class="text-muted">${esc(displayDate(row.date) || "—")}${row.reference ? ` · ${esc(row.reference)}` : ""}</div>${depositLinks}${itemLinks}</td>
            <td>${esc(row.loan_number || "—")}</td>
            <td class="text-right" style="white-space:nowrap">${amount(row.amount_usd)}</td>
            <td class="text-right" style="white-space:nowrap">${amount(row.pending_usd)}</td>
            <td>${esc(__(row.status || "Pendiente"))}${(row.observations || []).length ? `<div class="text-muted small">${row.observations.map(esc).join("<br>")}</div>` : ""}</td></tr>`;
    }).join("") || `<tr><td colspan="5">${esc(__(emptyMessage))}</td></tr>`;
    const panel = (title, items, emptyMessage, totalCents, net = false) => {
        const total = totalCents == null ? null : totalCents / 100;
        return `<section style="min-width:0">
        <h5 style="margin:0 0 10px">${esc(__(title))} (${items.length})</h5>
        <div class="table-responsive" style="max-height:60vh;overflow:auto"><table class="table table-bordered table-hover" style="font-size:12px;min-width:560px">
            <thead><tr>${["Documento / fecha / referencia", "Nro. Crédito", "Importe US$", "Pendiente US$", "Estado"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead>
            <tbody>${renderRows(items, emptyMessage)}</tbody>
            <tfoot><tr style="font-weight:600;background:var(--control-bg)">
                <td colspan="3">${esc(__(net ? "Total pendiente neto" : "Total pendiente"))}</td>
                <td class="text-right cn-related-pending-total" style="white-space:nowrap" title="${esc(total == null ? __("Total no determinable: hay un importe o sentido contable pendiente de revisar.") : "")}">${amount(total)}</td>
                <td></td></tr></tfoot>
        </table></div>${net ? `<p class="text-muted small" style="margin-top:6px">${esc(__("Total pendiente neto = depósitos + partidas de crédito − partidas de débito. Se usan los importes pendientes; los importes de las filas conservan su presentación original."))}</p>` : ""}</section>`;
    };
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
    const clientFilters = {
        ...(result.client_number ? {client_number: String(result.client_number)}
            : result.national_id ? {national_id: String(result.national_id)} : {}),
        ...(result.employer ? {employer: String(result.employer)} : {}),
    };
    const canFilterClient = Boolean(result.client_number || result.national_id);
    dialog.fields_dict.related_html.$wrapper.html(`${canFilterClient ? `<div style="display:flex;justify-content:flex-end;margin-bottom:12px"><button type="button" class="btn btn-default btn-sm cn-open-account-report">${esc(__("Ver Estado de Cuenta Operativo"))}</button></div>` : ""}
        <p class="text-muted">${esc(__("Registros pendientes de la misma empresa y cliente, aunque el crédito sea diferente. Verifique las coincidencias por nombre antes de conciliar. Solo se muestran documentos que tiene permiso de consultar."))}</p>
        <div class="cn-related-pending-summary" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px;margin-bottom:16px;align-items:stretch">
            <div style="padding:12px;background:var(--control-bg);border-radius:6px">
                <div class="small">${esc(__("Total aplicaciones pendientes US$"))}</div>
                <strong class="cn-related-pending-summary-applications" style="font-size:20px">${amount(applicationTotal == null ? null : applicationTotal / 100)}</strong>
            </div>
            <div style="padding:12px;background:var(--control-bg);border-radius:6px">
                <div class="small">${esc(__("Total neto depósitos y partidas US$"))}</div>
                <strong class="cn-related-pending-summary-sources" style="font-size:20px">${amount(sourceTotal == null ? null : sourceTotal / 100)}</strong>
            </div>
            <div class="alert ${differenceClass} cn-related-pending-difference" style="padding:12px;margin:0" role="status">
                <div class="small">${esc(__("Diferencia US$"))}</div>
                <strong class="cn-related-pending-difference-value" style="font-size:20px;white-space:nowrap">${amount(difference)}</strong>
                <div class="small">${esc(differenceExplanation)}</div>
            </div>
        </div>
        <p class="text-muted small">${esc(__("Aplicaciones pendientes − total neto de depósitos y partidas complementarias."))}</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px;align-items:start">
            ${panel("Aplicaciones pendientes", applications, "No se encontraron aplicaciones pendientes para este cliente.", applicationTotal)}
            ${panel("Depósitos y partidas complementarias pendientes", rows, "No se encontraron depósitos ni partidas complementarias pendientes para este cliente.", sourceTotal, true)}
        </div>
        <p class="text-muted small" style="margin-top:8px">${esc(__("«—» en el saldo de una aplicación indica que no se puede atribuir un pendiente individual con certeza; consulte su período."))}</p>`);
    if (canFilterClient) {
        dialog.fields_dict.related_html.$wrapper.on("click.cnRelatedPending", ".cn-open-account-report", () => {
            dialog.hide();
            frappe.set_route("query-report", "Estado de Cuenta Operativo", clientFilters);
        });
    }
    dialog.show();
};
