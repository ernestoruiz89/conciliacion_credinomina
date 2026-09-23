frappe.ui.form.on("CN Remittance Allocation", {
    refresh(frm) {
        frm.add_custom_button(__("Plantilla de detalle del depósito"), () => {
            const params = new URLSearchParams({ template_type: "deposito" });
            if (frm.doc.detail_period) params.set("period_name", frm.doc.detail_period);
            window.open(
                `/api/method/credinomina_reconciliation.template_download.download_import_template?${params}`,
                "_blank"
            );
        }, __("Plantillas"));
        const file = frm.doc.detail_file || frm.doc.support_file || "";
        if (frm.is_new() || frm.doc.docstatus === 2 || !/\.(xlsx|xls|csv)(\?|$)/i.test(file)) return;
        frm.add_custom_button(__("4. Cargar detalle del depósito"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.import_remittance_detail",
                args: { remittance_name: frm.doc.name },
                freeze: true,
                callback: () => frm.reload_doc(),
            });
        });
    },
    deposit_amount: updateUsdEquivalent,
    deposit_currency: updateUsdEquivalent,
    fx_rate: updateUsdEquivalent,
    deposit_date: updateUsdEquivalent,
});

function updateUsdEquivalent(frm) {
    const nativeAmount = Number(frm.doc.deposit_amount || 0);
    const currency = frm.doc.deposit_currency;
    const rate = Number(frm.doc.fx_rate || 0);
    const usd = currency === "USD" ? nativeAmount :
        currency === "NIO" && rate > 0 ? nativeAmount / rate : 0;
    frm.set_value("amount_usd", Number(usd.toFixed(4)));
}
