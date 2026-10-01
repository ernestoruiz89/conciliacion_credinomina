frappe.ui.form.on("CN Credit Portfolio Snapshot", {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__("Importar / actualizar corte"), () => importPortfolioSnapshot(frm));
    },
});

async function importPortfolioSnapshot(frm) {
    if (frm._cn_importing_portfolio) return;
    if (!frm.doc.source_file) {
        frappe.msgprint(__("Adjunte el archivo del corte de cartera antes de importar."));
        return;
    }
    frm._cn_importing_portfolio = true;
    let stage = "save";
    try {
        if (frm.is_dirty()) await frm.save();
        stage = "import";
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot.cn_credit_portfolio_snapshot.import_portfolio_snapshot",
            args: { snapshot_name: frm.doc.name },
            freeze: true,
            freeze_message: __("Importando créditos y verificando clientes y empresas..."),
        });
        if (!response.message || response.exc) throw new Error("missing_import_result");
        const result = response.message;
        const renamed = result.snapshot_name && result.snapshot_name !== frm.doc.name;
        if (renamed) {
            stage = "navigate";
            await frappe.set_route("Form", frm.doctype, result.snapshot_name);
        } else {
            stage = "reload";
            await frm.reload_doc();
        }
        const imported = renamed ? result : frm.doc;
        frappe.msgprint({
            title: result.unchanged ? __("Corte sin cambios") : __("Corte importado"),
            indicator: imported.status === "Importado con alertas" ? "orange" : "green",
            message: __("{0} créditos cargados. Clientes identificados: {1}. Clientes por revisar: {2}.", [
                imported.row_count || 0, imported.matched_client_count || 0, imported.unmatched_client_count || 0,
            ]) + (result.unchanged ? " " + __("Este archivo ya estaba importado; se conserva el detalle existente.")
                : " " + __("Empresas creadas: {0}. Clientes creados: {1}.", [
                    result.created_employer_count || 0, result.created_client_count || 0,
                ])),
        });
    } catch (error) {
        // A proxy/worker error may return HTML. Never parse it as JSON or inject it
        // into the message: the framework's own error renderer can fail here.
        const status = Number(error?.status) || 0;
        const statusText = status ? ` (HTTP ${status})` : "";
        const messages = {
            save: __("No se pudo guardar el archivo adjunto. La importación no se inició."),
            import: __("No se pudo completar la importación. El servidor devolvió un error o se interrumpió la conexión. Actualice el documento para verificar su estado; si continúa en borrador o sin filas, revise el registro de errores del servidor antes de reintentar."),
            reload: __("El servidor terminó la importación, pero no se pudo actualizar el formulario. Recargue la página para consultar el corte."),
            navigate: __("El corte se importó y renombró, pero no se pudo abrir con su nuevo identificador. Vuelva a la lista de cortes de cartera."),
        };
        frappe.msgprint({title: __("Importación de cartera"), indicator: "red", message: messages[stage] + statusText});
    } finally {
        frm._cn_importing_portfolio = false;
    }
}
