frappe.ui.form.on("CN Credit Portfolio Snapshot", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (!frm.is_dirty() && !frm._cn_saving_availability) {
            frm._cn_portfolio_saved = portfolioHeaderState(frm);
        }

        frm.add_custom_button(__("Importar / actualizar corte"), () => importPortfolioSnapshot(frm));
    },
    disabled(frm) {
        return savePortfolioAvailability(frm);
    },
});

function portfolioHeaderState(frm) {
    return Object.fromEntries(Object.entries(frm.doc).filter(([key, value]) =>
        !key.startsWith("_") && key !== "disabled" && key !== "modified" && key !== "modified_by"
        && (value === null || typeof value !== "object")));
}

async function savePortfolioAvailability(frm) {
    if (frm.is_new() || frm._cn_saving_availability || !frm._cn_portfolio_saved
        || frm._cn_portfolio_saved.name !== frm.doc.name) return;
    const desired = Number(frm.doc.disabled) ? 1 : 0;
    const modified = frm.doc.modified;
    frm._cn_saving_availability = true;
    frm.set_df_property("disabled", "read_only", 1);
    frm.disable_save(true);
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot.cn_credit_portfolio_snapshot.set_portfolio_disabled",
            args: {snapshot_name: frm.doc.name, disabled: desired, modified},
            freeze: true, freeze_message: __("Actualizando disponibilidad del corte..."),
        });
        if (!response.message || response.exc) throw new Error("missing_availability_result");
        Object.assign(frm.doc, response.message);
        frm.doc.__unsaved = JSON.stringify(portfolioHeaderState(frm)) === JSON.stringify(frm._cn_portfolio_saved) ? 0 : 1;
        frm.refresh_field("disabled");
        frappe.show_alert({message: desired ? __("Corte desactivado") : __("Corte activado"), indicator: "green"});
    } catch (error) {
        // A lost response may still have committed. Keep the form dirty and
        // require a reload to learn the authoritative state before retrying.
        frappe.msgprint(__("No se pudo confirmar el cambio de disponibilidad. Recargue el corte para verificar su estado antes de volver a intentarlo."));
    } finally {
        frm._cn_saving_availability = false;
        frm.set_df_property("disabled", "read_only", 0);
        frm.enable_save();
        frm.refresh_header();
    }
}

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
