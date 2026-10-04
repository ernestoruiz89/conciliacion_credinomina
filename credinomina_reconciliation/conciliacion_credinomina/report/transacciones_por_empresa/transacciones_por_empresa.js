frappe.query_reports["Transacciones por Empresa"] = {
    filters: [
        {fieldname: "year", label: __("Año"), fieldtype: "Int", reqd: 1,
            default: Number(frappe.datetime.get_today().slice(0, 4))},
        {fieldname: "transaction_type", label: __("Tipo de transacción"), fieldtype: "Select", reqd: 1,
            options: "Aplicaciones\nDepósitos", default: "Aplicaciones"},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "include_drafts", label: __("Incluir borradores"), fieldtype: "Check", default: 0},
    ],
    onload(report) {
        report.page.wrapper.off("click.cn_month_transactions", ".cn-month-transactions");
        report.page.wrapper.on("click.cn_month_transactions", ".cn-month-transactions", function(event) {
            event.preventDefault();
            const args = JSON.parse($(this).attr("data-detail"));
            frappe.query_reports["Transacciones por Empresa"].show_month_detail(args);
        });
    },
    formatter(value, row, column, data, default_formatter) {
        const key = column?.fieldname || "";
        if (data?._is_total) {
            const total = key === "employer" ? __("Total") : default_formatter(value, row, column, data);
            return `<div style="font-weight:700;text-align:${key === "employer" ? "left" : "right"}">${total}</div>`;
        }
        const html = default_formatter(value, row, column, data);
        if (key === "employer" && data && !value) return __("Sin empresa identificada");
        if (key === "total" && data) return `<div style="font-weight:bold;text-align:right">${html}</div>`;
        if (!/^m(0[1-9]|1[0-2])$/.test(key) || !data) {
            return html;
        }
        if (!Number(value)) return '<span class="text-muted" title="' + __("Sin transacciones") + '">—</span>';
        const state = data[key + "_state"] || "Pendiente";
        const colors = {Conciliado: ["#dcfce7", "#14532d"], Parcial: ["#fb923c", "#431407"], Pendiente: ["#fee2e2", "#991b1b"]};
        const percentage = data[key + "_percentage"];
        const known = percentage != null && Number.isFinite(Number(percentage));
        const [background, foreground] = state === "Parcial" && known && data[key + "_half_covered"]
            ? ["#fef9c3", "#713f12"] : colors[state] || colors.Pendiente;
        const count = suffix => Math.max(0, Number(data[key + suffix]) || 0);
        const formatAmount = amount => Number(amount).toLocaleString("es-NI", {minimumFractionDigits: 2, maximumFractionDigits: 2});
        const percentageLabel = known && Number(percentage) < 50 && Number(percentage).toFixed(2) === "50.00"
            ? "<50" : formatAmount(percentage);
        const progress = known
            ? `${__("Importe conciliado")}: ${percentageLabel}% · US$ ${formatAmount(data[key + "_covered_usd"])} / ${formatAmount(data[key + "_total_usd"])}`
            : __("Porcentaje del importe no disponible");
        const description = `${__(state)} · ${progress} · ${__("Conciliadas")}: ${count("_conciliado")} · ${__("Parciales")}: ${count("_parcial")} · ${__("Pendientes")}: ${count("_pendiente")}`;
        const escaped = frappe.utils.escape_html(description);
        // Offset DataTable's content padding so the status fills the cell.
        // Partial-state color uses the amount covered, never transaction count.
        const style = `background-color:${background};color:${foreground};width:calc(100% + 8px);margin:-4px;padding:4px;box-sizing:border-box;text-align:right;font-weight:600;border-radius:3px`;
        if (!data._detail_filters) return `<div style="${style}" title="${escaped}" aria-label="${escaped}">${html}</div>`;
        const args = frappe.utils.escape_html(JSON.stringify({...data._detail_filters, month: Number(key.slice(1))}));
        return `<button type="button" class="cn-month-transactions" data-detail="${args}" style="${style};border:0;font-family:inherit;font-size:inherit;cursor:pointer" title="${escaped}" aria-label="${escaped} · ${__("Ver transacciones del mes")}">${html}</button>`;
    },
    show_month_detail(args) {
        const esc = value => frappe.utils.escape_html(String(value ?? ""));
        const isApplication = args.transaction_type === "Aplicaciones";
        const money = value => value == null ? "—" : esc(format_currency(value, "USD"));
        const months = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
        let dialog, sequence = 0, offset = 0, closed = false, busy = false;
        let activeFilters = {state: "", search: ""};
        const load = async (start = 0) => {
            const request = ++sequence;
            offset = start;
            busy = true;
            const wrapper = dialog.fields_dict.detail.$wrapper;
            wrapper.html(`<p role="status">${__("Cargando transacciones…")}</p>`);
            try {
                const response = await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa.transacciones_por_empresa.get_month_detail",
                    args: {...args, ...activeFilters, start},
                });
                if (closed || request !== sequence) return;
                const data = response.message;
                if (!data?.rows) throw new Error("Respuesta sin detalle");
                const cards = data.summary.map(item => `<div style="flex:1;min-width:150px;padding:12px;border:1px solid var(--border-color);border-radius:8px"><div>${esc(item.label)}</div><strong style="font-size:20px">${esc(item.value)}</strong></div>`).join("");
                const headers = isApplication
                    ? ["Fecha", "Documento / fila", "Cliente", "Crédito", "Aplicado US$", "Ajuste US$", "Aplicado neto US$", "Asignado US$", "Redondeo US$", "Pendiente US$", "Estado"]
                    : ["Fecha", "Depósito", "Referencia / asiento", "Cuenta bancaria", "Depositado US$", "Asignado US$", "Saldo a favor US$", "Sin distribuir US$", "Estado"];
                const body = data.rows.map(item => {
                    const colors = {Conciliado: ["#dcfce7", "#14532d"], Parcial: ["#ffedd5", "#9a3412"], Pendiente: ["#fee2e2", "#991b1b"]};
                    const [background, color] = colors[item.state] || colors.Pendiente;
                    const badge = `<span style="display:inline-block;padding:3px 8px;border-radius:12px;background:${background};color:${color}">${esc(item.state)}</span>`;
                    const extra = [item.status_detail, item.reason, item.observations].filter(Boolean).join(" · ");
                    const status = `${badge}${extra ? `<details style="margin-top:6px"><summary>${__("Ver motivo")}</summary><div style="min-width:200px;white-space:normal">${esc(extra)}</div></details>` : ""}`;
                    const slug = isApplication ? "cn-accounting-import" : "cn-remittance-allocation";
                    const link = `<a href="/app/${slug}/${encodeURIComponent(item.document)}" target="_blank" rel="noopener noreferrer">${esc(item.document)}</a>`;
                    const date = item.date ? esc(frappe.datetime.str_to_user(item.date)) : "—";
                    const cells = isApplication
                        ? [date, `${link}<div>${__("Fila")} ${esc(item.row_index)}</div><div>${esc([item.reference, item.voucher, item.receipt].filter(Boolean).join(" · "))}</div>`,
                            `${esc(item.client_name || __("Sin identificar"))}<div>${__("Nro. Cliente")}: ${esc(item.client_number || "—")}</div>`, esc(item.loan_number || "—"),
                            money(item.original_usd), money(item.adjustment_usd), money(item.net_usd), money(item.assigned_usd), money(item.rounding_usd), money(item.pending_usd), status]
                        : [date, link, esc([item.reference, item.voucher].filter(Boolean).join(" · ")),
                            `${esc(item.bank_account || "—")}<div>${esc(item.original_currency)} ${esc(item.original_amount)}</div>`,
                            money(item.original_usd), money(item.assigned_usd), money(item.surplus_usd), money(item.pending_usd), status];
                    return `<tr>${cells.map((cell, index) => `<td style="vertical-align:top;${index >= 4 && index < cells.length - 1 ? "text-align:right;white-space:nowrap" : ""}">${cell}</td>`).join("")}</tr>`;
                }).join("");
                const amountFields = isApplication
                    ? ["original_usd", "adjustment_usd", "net_usd", "assigned_usd", "rounding_usd", "pending_usd"]
                    : ["original_usd", "assigned_usd", "surplus_usd", "pending_usd"];
                const footer = `<tfoot><tr style="font-weight:700;background:var(--control-bg)"><th colspan="4">${__("Total filtrado")} · ${esc(data.filtered_count)} ${__("transacciones")}</th>
                    ${amountFields.map(field => `<td style="text-align:right;white-space:nowrap">${money(data.totals?.[field])}</td>`).join("")}<td></td></tr></tfoot>`;
                const end = start + data.rows.length;
                wrapper.html(`<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px">${cards}</div>
                    <p class="text-muted">${__("Los indicadores cuentan transacciones de esta empresa y mes, no celdas del calendario.")}</p>
                    <div style="overflow:auto;max-height:55vh"><table class="table table-bordered" style="font-size:12px;line-height:1.4"><thead><tr>${headers.map(label => `<th style="white-space:nowrap;position:sticky;top:0;background:var(--fg-color);z-index:1">${__(label)}</th>`).join("")}</tr></thead>
                    <tbody>${body || `<tr><td colspan="${headers.length}">${__("No hay transacciones con estos filtros.")}</td></tr>`}</tbody>${footer}</table></div>
                    <p class="text-muted">${__("El total incluye todas las páginas con los filtros actuales. Importes en US$. — indica que falta conversión o atribución individual; el total de esa columna también queda sin determinar.")}</p>
                    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><span>${esc(data.rows.length ? start + 1 : 0)}–${esc(end)} / ${esc(data.filtered_count)} ${__("transacciones")}</span>
                    <div><button class="btn btn-default btn-sm cn-detail-previous" ${start === 0 ? "disabled" : ""}>${__("Anterior")}</button>
                    <button class="btn btn-default btn-sm cn-detail-next" ${end >= data.filtered_count ? "disabled" : ""}>${__("Siguiente")}</button></div></div>`);
            } catch (error) {
                if (!closed && request === sequence) wrapper.html(`<div class="alert alert-danger">${__("No se pudo cargar el detalle. Pulse Buscar / actualizar para reintentar.")}</div>`);
            } finally {
                if (request === sequence) busy = false;
            }
        };
        dialog = new frappe.ui.Dialog({
            title: __("Transacciones del mes"), size: "extra-large",
            fields: [
                {fieldname: "context", fieldtype: "HTML", options: `<p style="font-size:16px"><strong>${esc(args.employer || __("Sin empresa identificada"))}</strong> · ${__(months[args.month - 1])} ${esc(args.year)} · ${esc(args.transaction_type)}${Number(args.include_drafts) ? ` · ${__("Incluye borradores")}` : ""}</p>`},
                {fieldname: "state", label: __("Estado"), fieldtype: "Select", options: "\nConciliado\nParcial\nPendiente"},
                {fieldtype: "Column Break"},
                {fieldname: "search", label: __("Buscar"), fieldtype: "Data", description: __("Cliente, crédito, documento, referencia, asiento o cuenta bancaria.")},
                {fieldtype: "Section Break"},
                {fieldname: "detail", fieldtype: "HTML"},
            ],
            primary_action_label: __("Buscar / actualizar"),
            primary_action() {
                activeFilters = {state: dialog.get_value("state") || "", search: dialog.get_value("search") || ""};
                load(0);
            },
            onhide() { closed = true; sequence++; },
        });
        dialog.fields_dict.detail.$wrapper.on("click", ".cn-detail-previous", () => { if (!busy) load(Math.max(0, offset - 100)); });
        dialog.fields_dict.detail.$wrapper.on("click", ".cn-detail-next", () => { if (!busy) load(offset + 100); });
        dialog.show();
        load(0);
        return dialog;
    },
};
