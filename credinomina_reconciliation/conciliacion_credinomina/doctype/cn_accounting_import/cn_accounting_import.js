function update_currency_fields(frm) {
    const is_nio = frm.doc.currency === "NIO";
    frm.toggle_reqd("currency", frm.is_new() || frm.doc.status === "Borrador");
    frm.toggle_display("manual_fx_rate", is_nio);
    frm.toggle_reqd("manual_fx_rate", is_nio);
}

frappe.ui.form.on("CN Accounting Import", {
    refresh(frm) {
        update_currency_fields(frm);
        if (frappe.model.can_create("CN Accounting Import")) {
            frm.add_custom_button(__("Carga masiva"), () => {
                if (typeof frappe.credinomina?.openAccountingBulk !== "function") {
                    frappe.msgprint(__("No se cargó la herramienta de carga masiva. Recargue la página; si persiste, solicite limpiar la caché del sitio después de actualizar la app."));
                    return;
                }
                frappe.credinomina.openAccountingBulk();
            });
        }
        frm.set_query("employer", () => ({filters: {active: 1}}));
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("portfolio_snapshot", () => ({
            query: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.get_company_portfolio_snapshots",
            filters: {employer: frm.doc.employer},
        }));
        frm.set_df_property("rows", "label", __("Aplicaciones de pago por cliente"));
        updateImportExceptionNotice(frm);
        if (frm.is_new()) return;

        frm.add_custom_button(__("3. Cargar movimientos contables"), async () => {
            if (frm.is_dirty()) await frm.save();
            return frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.import_source_file",
                args: { import_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando aplicaciones y actualizando conciliaciones..."),
            }).then(response => {
                const name = response.message?.import_name;
                if (name && name !== frm.doc.name) {
                    return frappe.set_route("Form", frm.doctype, name);
                }
                return frm.reload_doc();
            });
        });

        if (frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Conciliar esta empresa"), () => reconcileCompany(frm));
        }
    },

    currency(frm) {
        if (frm.doc.currency !== "NIO") {
            frm.set_value("manual_fx_rate", 0);
        }
        update_currency_fields(frm);
    },

    employer(frm) {
        frm.set_value("portfolio_snapshot", "");
        frm.set_value("historical_period", "");
    },
});

function importExceptionRows(rows) {
    // Match CNAccountingImport.recalculate_summary: a file exception is not
    // necessarily an import error or a CN Reconciliation Exception document.
    return (rows || []).flatMap(row => {
        const reasons = [];
        if (["Ambiguo", "Sin coincidencia"].includes(row.match_status)) {
            reasons.push({stage: __("Identificación / vinculación"),
                reason: row.match_reason || __("No hay una coincidencia única. Revise cliente, crédito, empresa y período.")});
        }
        if (row.event_type === "Aplicacion" && Number(row.effective)
                && !["Depósito conciliado", "Aplicación compensada"].includes(row.deposit_match_status)) {
            if (!["Conciliado", "Ambiguo", "Sin coincidencia"].includes(row.match_status)) {
                reasons.push({stage: __("Aplicación"),
                    reason: row.match_reason || __("La aplicación aún no tiene un vínculo confirmado con el período o la cobranza.")});
            }
            reasons.push({stage: __("Depósito de la aplicación"),
                reason: row.deposit_match_reason || __("Falta conciliar el depósito de esta aplicación. Registre o complete su distribución y vuelva a conciliar.")});
        }
        if (row.event_type === "Deposito" && Number(row.effective)
                && Number(row.unallocated_usd || 0) > 0.005) {
            reasons.push({stage: __("Distribución del depósito"),
                reason: row.allocation_reason || __("El depósito tiene saldo sin distribuir. Revise sus destinos y justifique cualquier excedente.")});
        }
        return reasons.length ? [{row, reasons}] : [];
    });
}

function updateImportExceptionNotice(frm) {
    const exceptions = importExceptionRows(frm.doc.rows);
    if (!exceptions.length && frm.doc.status !== "Importado con excepciones") {
        frm.set_intro("");
        return;
    }
    frm.set_intro(exceptions.length
        ? __("Esta carga tiene {0} filas con excepciones de conciliación. Use «Ver excepciones» para conocer los motivos; puede tratarse de depósitos pendientes, no de un error al importar.", [exceptions.length])
        : __("La carga conserva el estado «Importado con excepciones», pero las filas actuales no muestran excepciones. Revise el detalle y use «Conciliar esta empresa» para actualizar el resultado."), "orange");
    frm.add_custom_button(__("Ver excepciones"), () => showImportExceptions(frm));
}

function importExceptionsHtml(frm) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const exceptions = importExceptionRows(frm.doc.rows);
    const summary = new Map();
    for (const {reasons} of exceptions) {
        for (const stage of new Set(reasons.map(reason => reason.stage))) {
            summary.set(stage, (summary.get(stage) || 0) + 1);
        }
    }
    return `<p>${__("Los motivos corresponden únicamente a esta carga. Una aplicación puede estar importada y vinculada al período, pero seguir pendiente de depósito.")}</p>
        <p><strong>${__("Filas con excepciones")}: ${esc(exceptions.length)}</strong></p>
        ${exceptions.length ? `
            <ul>${[...summary].map(([stage, count]) => `<li>${esc(stage)}: ${esc(count)}</li>`).join("")}</ul>
            <p class="text-muted">${__("Una fila puede tener varios motivos; los conteos por etapa no se suman.")}</p>
            <div style="max-height:55vh;overflow:auto"><table class="table table-bordered">
                <thead><tr><th>${__("Fila del archivo / tabla")}</th><th>${__("Cliente / crédito")}</th><th>${__("Etapa y motivo")}</th></tr></thead>
                <tbody>${exceptions.map(({row, reasons}) => `<tr>
                    <td>${esc(row.source_row || row.idx)} / ${esc(row.idx)}</td>
                    <td>${esc(row.client_name)}<br>${__("Nro. Cliente")}: ${esc(row.client_number)}<br>${__("Crédito")}: ${esc(row.loan_number)}</td>
                    <td style="white-space:normal">${reasons.map(({stage, reason}) => `<p><strong>${esc(stage)}</strong><br>${esc(reason)}</p>`).join("")}</td>
                </tr>`).join("")}</tbody>
            </table></div>`
            : `<p>${__("No hay excepciones en las filas actuales. Si el estado guardado no coincide, use «Conciliar esta empresa» para recalcularlo.")}</p>`}
        <p class="text-muted">${__("Este detalle no guarda cambios ni ejecuta una conciliación. Las filas inactivas, como los duplicados descartados, no se consideran pendientes de depósito.")}</p>`;
}

function showImportExceptions(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Excepciones de {0}", [frm.doc.name]), size: "extra-large",
        fields: [{fieldtype: "HTML", options: importExceptionsHtml(frm)}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
    });
    dialog.show();
}

async function reconcileCompany(frm) {
    if (frm.__company_reconciliation_running) return;
    if (!frm.doc.employer) {
        frappe.msgprint(__("Seleccione la empresa antes de conciliar."));
        return;
    }
    frm.__company_reconciliation_running = true;
    try {
        if (frm.is_dirty()) await frm.save();
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import.reconcile_company_sources",
            args: {import_name: frm.doc.name},
            freeze: true,
            freeze_message: __("Conciliando las importaciones y depósitos de {0}…", [frm.doc.employer]),
        });
        await frm.reload_doc();
        showCompanyReconciliation(response.message);
    } finally {
        frm.__company_reconciliation_running = false;
    }
}

function showCompanyReconciliation(result) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const counts = [
        [__("Filas procesadas"), result.rows], [__("Conciliadas"), result.matched],
        [__("Pendientes"), result.pending], [__("Ignoradas"), result.ignored],
    ];
    const pending = result.pending_rows || [];
    const matched = result.matched_rows || [];
    const renderMovements = (movements, reconciled) => `
        <div style="max-height:40vh;overflow:auto"><table class="table table-bordered">
        <thead><tr><th>${__("Importación / fila")}</th><th>${__("Cliente / crédito")}</th><th>${__("Estado")}</th><th>${__("Detalle")}</th></tr></thead>
        <tbody>${movements.map(row => `<tr${reconciled ? ' class="cn-row-reconciled"' : ""}>
            <td><a href="/app/cn-accounting-import/${encodeURIComponent(row.import_name)}">${esc(row.import_name)}</a><br>${__("Fila")} ${esc(row.row)}</td>
            <td>${esc(row.client_name)}<br><span class="text-muted">${esc(row.loan_number)}</span></td>
            <td>${reconciled ? __("Conciliado") : __("Pendiente")}</td>
            <td style="white-space:normal">${esc(row.reason)}</td>
        </tr>`).join("")}</tbody></table></div>`;
    const dialog = new frappe.ui.Dialog({
        title: __("Conciliación de {0}", [result.employer]), size: "extra-large",
        fields: [{fieldtype: "HTML", options: `
            <style>.cn-company-reconciliation .cn-row-reconciled > td {
                background-color: var(--bg-green, #eaf6ec);
                color: var(--text-color, #1f272e);
            }</style>
            <div class="cn-company-reconciliation">
            <p>${__("Se procesaron {0} importaciones de esta empresa con los datos guardados.", [esc(result.imports)])}</p>
            <div class="row">${counts.map(([label, value]) => `<div class="col-sm-3"><div class="text-muted">${label}</div><h3>${esc(value)}</h3></div>`).join("")}</div>
            <p class="text-muted">${__("Las filas ignoradas, como duplicados y ajustes, no se cuentan como pendientes.")}</p>
            ${result.pending ? `<h5>${__("Motivos para revisar")}</h5>
                ${renderMovements(pending, false)}
                ${result.pending > pending.length ? `<p>${__("Se muestran {0} de {1} filas pendientes. Abra las importaciones para revisar las demás.", [esc(pending.length), esc(result.pending)])}</p>` : ""}`
                : `<p class="text-success">${result.rows ? __("No quedaron filas pendientes de conciliación.") : __("Esta empresa todavía no tiene movimientos importados.")}</p>`}
            ${matched.length ? `<h5>${__("Movimientos conciliados")}</h5>
                ${renderMovements(matched, true)}
                ${result.matched > matched.length ? `<p>${__("Se muestran {0} de {1} filas conciliadas. Abra las importaciones para revisar las demás.", [esc(matched.length), esc(result.matched)])}</p>` : ""}` : ""}
            </div>
        `}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        secondary_action_label: __("Ver importaciones"),
        secondary_action: () => {
            dialog.hide();
            frappe.set_route("List", "CN Accounting Import", {employer: result.employer});
        },
    });
    dialog.show();
}
