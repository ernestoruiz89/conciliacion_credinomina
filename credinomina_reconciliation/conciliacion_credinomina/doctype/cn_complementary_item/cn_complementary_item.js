frappe.ui.form.on("CN Complementary Item", {
    refresh(frm) {
        if (frm.doc.docstatus === 2) return;
        if (!frm.doc.voucher) {
            frm.dashboard.set_headline_alert(__("Pendiente de registro contable: complete el Asiento contable cuando se registre, incluso si la partida ya está confirmada."), "orange");
        } else {
            frm.dashboard.clear_headline();
        }
    },
});
