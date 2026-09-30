frappe.ui.form.on("CN Complementary Item", {
    setup(frm) {
        frm.set_query("registered_deposit", () => ({filters: {docstatus: 1,
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
        frm.set_query("period", () => ({filters: {status: ["!=", "Cerrado"],
            ...(frm.doc.employer ? {employer: frm.doc.employer} : {})}}));
    },
    category(frm) {
        const credit = frm.doc.category === "Saldo a favor de la empresa";
        // Keep invalid prefilled identities visible so the user can clear them.
        ["client_number", "loan_number", "installment_number"].forEach(f => frm.toggle_display(f, !credit || !!frm.doc[f]));
        frm.set_df_property("reference", "read_only", credit);
    },
    async registered_deposit(frm) {
        if (!frm.doc.registered_deposit || frm.doc.category !== "Saldo a favor de la empresa") return;
        const name = frm.doc.registered_deposit;
        const r = await frappe.db.get_value("CN Remittance Allocation", name,
            ["employer", "deposit_reference", "deposit_voucher", "deposit_date"]);
        if (frm.doc.registered_deposit !== name || frm.doc.category !== "Saldo a favor de la empresa") return;
        await frm.set_value({employer: r.message.employer, reference: r.message.deposit_reference,
            deposit_voucher: r.message.deposit_voucher, posting_date: frm.doc.posting_date || r.message.deposit_date});
    },
    refresh(frm) {
        frm.trigger("category");
        if (frm.doc.registered_deposit) frm.add_custom_button(__("Abrir depósito"),
            () => frappe.set_route("Form", "CN Remittance Allocation", frm.doc.registered_deposit));
        if (frm.doc.docstatus === 2) return;
        if (!frm.doc.voucher) {
            frm.dashboard.set_headline_alert(__("Pendiente de registro contable: complete el Asiento contable cuando se registre, incluso si la partida ya está confirmada."), "orange");
        } else {
            frm.dashboard.clear_headline();
        }
    },
});
