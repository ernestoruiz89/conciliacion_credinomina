frappe.ui.form.on("CN Client", {
    refresh(frm) {
        if (frm.is_new()) return;
        const button = frm.add_custom_button(__("Ver partidas vinculadas"), () => showClientRelatedPending(frm, button));
    },
});

async function showClientRelatedPending(frm, button) {
    if (frm._client_pending_loading) return;
    if (frm.is_dirty()) {
        frappe.msgprint(__("Guarde o recargue los cambios del cliente antes de consultar sus partidas pendientes."));
        return;
    }
    const client = frm.doc.name, employer = frm.doc.employer;
    frm._client_pending_loading = true;
    button?.prop("disabled", true);
    try {
        const response = await frappe.call({
            method: "credinomina_reconciliation.reconciliation_workbench.get_data",
            args: {employer, client},
            freeze: true,
            freeze_message: __("Consultando partidas pendientes del cliente…"),
        });
        if (frm.doc.name !== client || frm.doc.employer !== employer || frm.is_dirty()) return;
        await frappe.require("/assets/credinomina_reconciliation/js/related_pending_modal.js");
        if (frm.doc.name !== client || frm.doc.employer !== employer || frm.is_dirty()) return;
        window.credinominaShowRelatedPending({...response.message, client});
    } catch (error) {
        if (frm.doc.name === client && frm.doc.employer === employer) {
            frappe.msgprint(__("No se pudieron consultar las partidas pendientes. Revise sus permisos o vuelva a intentar."));
        }
    } finally {
        frm._client_pending_loading = false;
        button?.prop("disabled", false);
    }
}
