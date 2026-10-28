frappe.provide("credinomina");

credinomina.document_difference = async function (options) {
    const api = "credinomina_reconciliation.difference_documentation.";
    const scope = {doctype: options.doctype, name: options.name,
        parentfield: options.parentfield || "", row_name: options.row_name || ""};
    const detailRow = scope.doctype === "CN Remittance Allocation" && scope.parentfield === "detail_rows";
    const response = await frappe.call({method: api + "get_documentation", args: scope});
    const saved = response.message;
    let busy = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Clasificar / documentar diferencia"), size: "large",
        fields: [
            {fieldtype: "HTML", options: `<p>${frappe.utils.escape_html(__("Documentar conserva los importes y el estado de conciliación. Puede adjuntar soportes al documento de origen."))}</p>`},
            {fieldname: "difference_reason", label: __("Razón de la diferencia"), fieldtype: "Link",
                options: "CN Difference Reason", reqd: 1, default: saved.difference_reason},
            {fieldname: "difference_notes", label: __(detailRow ? "Comentarios de la diferencia" : "Documentación / soporte"), fieldtype: "Small Text",
                reqd: detailRow ? 0 : 1, default: saved.difference_notes},
        ],
        primary_action_label: __("Guardar documentación"),
        async primary_action(values) {
            if (busy) return;
            busy = true;
            dialog.disable_primary_action();
            try {
                await frappe.call({method: api + "document_difference", args: {...scope,
                    difference_reason: values.difference_reason, difference_notes: values.difference_notes || "",
                    modified: saved.modified}});
                dialog.hide();
                frappe.show_alert({message: __("Diferencia documentada. Los saldos se conservan."), indicator: "green"});
                if (options.on_saved) await options.on_saved();
            } finally { busy = false; dialog.enable_primary_action(); }
        },
    });
    dialog.show();
    return dialog;
};

for (const doctype of ["CN Remittance Allocation", "CN Complementary Item"]) {
    frappe.ui.form.on(doctype, {
        refresh(frm) {
            if (doctype === "CN Complementary Item" && frm.is_new()) {
                frm.disable_save();
                frm.dashboard.set_headline_alert(__("Las partidas nuevas se importan del core. Para una diferencia, abra el depósito, aplicación o cobranza y use Clasificar / documentar diferencia."), "orange");
                return;
            }
            if (frm.is_new() || frm.doc.docstatus === 2 || !frm.get_perm(0, "write")) return;
            frm.add_custom_button(__("Clasificar / documentar diferencia"), () => {
                if (frm.is_dirty()) return frappe.msgprint(__("Guarde el documento antes de documentar la diferencia."));
                return credinomina.document_difference({doctype, name: frm.doc.name, on_saved: () => frm.reload_doc()});
            });
            if (doctype === "CN Complementary Item" && frm.doc.legacy_manual_review) {
                frm.dashboard.set_headline_alert(__("Partida histórica conservada para revisión. Sus importes y vínculos no se modificaron en la migración."), "orange");
                frm.add_custom_button(__("Revisar partida histórica"), () => frappe.set_route("query-report", "Revision de Partidas Manuales", {item: frm.doc.name, include_tolerance: 1}));
            }
        },
    });
}

for (const [doctype, parentfield] of [["CN Source Row", "rows"], ["CN Collection Row", "collection_rows"]]) {
    frappe.ui.form.on(doctype, {
        document_difference(frm, cdt, cdn) {
            if (frm.is_new() || frm.is_dirty()) return frappe.msgprint(__("Guarde el documento antes de documentar la diferencia."));
            return credinomina.document_difference({doctype: frm.doc.doctype, name: frm.doc.name,
                parentfield, row_name: cdn, on_saved: () => frm.reload_doc()});
        },
    });
}
