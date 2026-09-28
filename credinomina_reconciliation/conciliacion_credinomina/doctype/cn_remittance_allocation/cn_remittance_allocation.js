frappe.ui.form.on("CN Remittance Allocation", {
    refresh(frm) {
        showDepositStage(frm);
        frm.add_custom_button(__("Plantilla de detalle del depósito"), () => {
            const params = new URLSearchParams({ template_type: "deposito" });
            if (frm.doc.detail_period) params.set("period_name", frm.doc.detail_period);
            window.open(
                `/api/method/credinomina_reconciliation.template_download.download_import_template?${params}`,
                "_blank"
            );
        }, __("Plantillas"));
        if (!frm.is_new() && frm.doc.docstatus === 0 && frm.get_perm(0, "submit")) {
            frm.add_custom_button(__("Confirmar depósito y conciliar"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.savesubmit();
            });
        }
        const file = frm.doc.detail_file || frm.doc.support_file || "";
        if (frm.is_new() || frm.doc.docstatus === 2 || !/\.(xlsx|xls|csv)(\?|$)/i.test(file)) return;
        frm.add_custom_button(__("Cargar detalle del depósito"), async () => {
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

function showDepositStage(frm) {
    if (frm.is_new()) {
        frm.set_intro(__("Guarde el depósito. Puede confirmarlo antes de recibir el detalle por cliente; el detalle podrá cargarse después."), "blue");
    } else if (frm.doc.docstatus === 0) {
        frm.set_intro(
            frm.get_perm(0, "submit")
                ? __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; use Confirmar depósito y conciliar.")
                : __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; solicite a un supervisor que confirme el depósito."),
            "orange"
        );
    } else if (frm.doc.docstatus === 2) {
        frm.set_intro(__("Depósito cancelado: no participa en la conciliación."), "red");
    } else if (!frm.doc.detail_count && !(frm.doc.targets || []).length) {
        frm.set_intro(__("Depósito confirmado, pendiente de detalle por cliente o distribución manual documentada."), "orange");
    } else if (["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(frm.doc.result)) {
        frm.set_intro(__("Depósito confirmado, pero hay detalle o destinos pendientes de revisión."), "orange");
    } else if (frm.doc.result === "Conciliado") {
        frm.set_intro(__("Depósito confirmado y conciliado."), "green");
    } else {
        frm.set_intro(__("Depósito confirmado. Revise el resultado y el saldo sin distribuir."), "blue");
    }
}

function updateUsdEquivalent(frm) {
    const nativeAmount = Number(frm.doc.deposit_amount || 0);
    const currency = frm.doc.deposit_currency;
    const rate = Number(frm.doc.fx_rate || 0);
    const usd = currency === "USD" ? nativeAmount :
        currency === "NIO" && rate > 0 ? nativeAmount / rate : 0;
    frm.set_value("amount_usd", Number(usd.toFixed(4)));
}
