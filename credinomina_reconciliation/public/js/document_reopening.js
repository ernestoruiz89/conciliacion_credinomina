frappe.ui.form.on("CN Complementary Item", {refresh: cnAddUndoCancellation});
frappe.ui.form.on("CN Remittance Allocation", {refresh: cnAddUndoCancellation});
frappe.ui.form.on("CN Accounting Import", {refresh: cnAddUndoCancellation});

function cnAddUndoCancellation(frm) {
    frm.remove_custom_button?.(__("Deshacer cancelación"));
    if (frm.is_new() || frm.doc.docstatus !== 2 || !frm.get_perm(0, "write") || !frm.get_perm(0, "delete")
            || frm.toolbar?.has_workflow?.()) return;
    if (frm.meta?.is_submittable && !frm.get_perm(0, "cancel")) return;
    frm.add_custom_button(__("Deshacer cancelación"), () => cnUndoCancellation(frm));
}

function cnUndoCancellation(frm) {
    if (frm._cn_reopening || frm.doc.docstatus !== 2) return;
    if (frm.is_dirty()) return frappe.msgprint(__("Recargue el documento antes de deshacer la cancelación."));
    const name = frm.doc.name, modified = frm.doc.modified;
    const dialog = new frappe.ui.Dialog({
        title: __("Deshacer cancelación"),
        fields: [
            {fieldtype: "HTML", options: `<p>${__("El documento volverá a borrador con el mismo número. Revise sus vínculos antes de confirmarlo y conciliarlo nuevamente.")}</p>`},
            {fieldname: "reason", fieldtype: "Small Text", label: __("Motivo"), reqd: 1},
        ],
        primary_action_label: __("Volver a borrador"),
        async primary_action(values) {
            if (frm._cn_reopening) return;
            if (frm.doc.name !== name || frm.doc.modified !== modified || frm.is_dirty()) {
                return frappe.msgprint(__("El documento cambió. Recargue el formulario antes de continuar."));
            }
            frm._cn_reopening = true;
            dialog.disable_primary_action();
            try {
                await frappe.call({method: "credinomina_reconciliation.document_reopening.undo_cancellation",
                    args: {doctype: frm.doc.doctype, name, modified, reason: values.reason},
                    freeze: true, freeze_message: __("Deshaciendo cancelación…")});
                dialog.hide();
                if (frm.doc.name === name) await frm.reload_doc();
                frappe.show_alert({message: __("Cancelación deshecha. Documento en borrador."), indicator: "green"});
            } finally {
                frm._cn_reopening = false;
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();
}
