function update_currency_fields(frm) {
    const is_nio = frm.doc.currency === "NIO";
    frm.toggle_reqd("currency", frm.is_new() || frm.doc.status === "Borrador");
    frm.toggle_display("manual_fx_rate", is_nio);
    frm.toggle_reqd("manual_fx_rate", is_nio);
}

frappe.ui.form.on("CN Source Import", {
    refresh(frm) {
        update_currency_fields(frm);
        frm.set_df_property("source_type", "read_only", 1);
        frm.set_query("employer", () => ({filters: {active: 1}}));
        frm.set_query("historical_period", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("historical_period", "rows", () => ({
            filters: { reconciliation_mode: "Historica", employer: frm.doc.employer },
        }));
        frm.set_query("portfolio_snapshot", () => ({
            query: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.get_company_portfolio_snapshots",
            filters: {employer: frm.doc.employer},
        }));
        frm.set_df_property("rows", "label", __("Aplicaciones de pago por cliente"));
        if (frm.is_new()) return;

        frm.add_custom_button(__("3. Cargar movimientos contables"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.import_source_file",
                args: { import_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando aplicaciones y actualizando conciliaciones..."),
            }).then(() => frm.reload_doc());
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
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import.reconcile_company_sources",
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
    const rows = pending.map(row => `<tr>
        <td><a href="/app/cn-source-import/${encodeURIComponent(row.import_name)}">${esc(row.import_name)}</a><br>${__("Fila")} ${esc(row.row)}</td>
        <td>${esc(row.client_name)}<br><span class="text-muted">${esc(row.loan_number)}</span></td>
        <td style="white-space:normal">${esc(row.reason)}</td>
    </tr>`).join("");
    const dialog = new frappe.ui.Dialog({
        title: __("Conciliación de {0}", [result.employer]), size: "extra-large",
        fields: [{fieldtype: "HTML", options: `
            <p>${__("Se procesaron {0} importaciones de esta empresa con los datos guardados.", [esc(result.imports)])}</p>
            <div class="row">${counts.map(([label, value]) => `<div class="col-sm-3"><div class="text-muted">${label}</div><h3>${esc(value)}</h3></div>`).join("")}</div>
            <p class="text-muted">${__("Las filas ignoradas, como duplicados y ajustes, no se cuentan como pendientes.")}</p>
            ${result.pending ? `<h5>${__("Motivos para revisar")}</h5>
                <div style="max-height:45vh;overflow:auto"><table class="table table-bordered">
                <thead><tr><th>${__("Importación / fila")}</th><th>${__("Cliente / crédito")}</th><th>${__("Motivo")}</th></tr></thead><tbody>${rows}</tbody></table></div>
                ${result.pending > pending.length ? `<p>${__("Se muestran {0} de {1} filas pendientes. Abra las importaciones para revisar las demás.", [esc(pending.length), esc(result.pending)])}</p>` : ""}`
                : `<p class="text-success">${result.rows ? __("No quedaron filas pendientes de conciliación.") : __("Esta empresa todavía no tiene movimientos importados.")}</p>`}
        `}],
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        secondary_action_label: __("Ver importaciones"),
        secondary_action: () => {
            dialog.hide();
            frappe.set_route("List", "CN Source Import", {employer: result.employer});
        },
    });
    dialog.show();
}
