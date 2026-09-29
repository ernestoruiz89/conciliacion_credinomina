frappe.ui.form.on("CN Remittance Allocation", {
    setup(frm) {
        frm.set_query("bank_account", () => ({ filters: { active: 1 } }));
    },
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
            frm.add_custom_button(__("Confirmar depósito"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frm.savesubmit();
            });
        }
        if (!frm.is_new() && frm.doc.docstatus === 1 && frm.get_perm(0, "write")) {
            frm.add_custom_button(__("Conciliar"), async () => {
                if (frm.is_dirty()) await frm.save();
                await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.reconcile_remittance",
                    args: { remittance_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Conciliando depósito…"),
                });
                await frm.reload_doc();
            }, __("Conciliación"));
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
        frm.set_intro(__("Guarde el depósito y confírmelo cuando sus datos estén completos. Confirmar no ejecuta la conciliación; podrá conciliar por separado."), "blue");
    } else if (frm.doc.docstatus === 0) {
        frm.set_intro(
            frm.get_perm(0, "submit")
                ? __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; use Confirmar depósito y luego Conciliar.")
                : __("Depósito en borrador: todavía no participa en la conciliación. Cargar el detalle no lo confirma; solicite a un supervisor que confirme el depósito."),
            "orange"
        );
    } else if (frm.doc.docstatus === 2) {
        frm.set_intro(__("Depósito cancelado: no participa en la conciliación."), "red");
    } else if (frm.doc.detail_status === "Cargado; pendiente de conciliación") {
        frm.set_intro(__("Depósito confirmado; el detalle está cargado y pendiente de conciliación. Use Conciliar cuando quiera actualizar el resultado."), "orange");
    } else if (frm.doc.result === "Pendiente") {
        frm.set_intro(__("Depósito confirmado, pendiente de conciliación. Use Conciliar para calcular la distribución y el resultado."), "orange");
    } else if (!frm.doc.detail_count && !(frm.doc.targets || []).length) {
        frm.set_intro(__("Depósito confirmado, pendiente de detalle por cliente o distribución manual documentada. La conciliación se ejecuta por separado."), "orange");
    } else if (["Revisar detalle", "Revisar destinos", "Detalle pendiente"].includes(frm.doc.result)) {
        frm.set_intro(__("Depósito confirmado, pero hay detalle o destinos pendientes de revisión."), "orange");
    } else if (frm.doc.result === "Conciliado") {
        frm.set_intro(__("Depósito confirmado y conciliado."), "green");
    } else {
        frm.set_intro(__("Depósito confirmado. Revise el resultado y el saldo sin distribuir."), "blue");
    }
}

function updateUsdEquivalent(frm) {
    const amountCents = toScaledInteger(frm.doc.deposit_amount, 2);
    const currency = frm.doc.deposit_currency;
    const rateScaled = toScaledInteger(frm.doc.fx_rate, 8);
    let usdCents = 0n;
    if (currency === "USD") {
        usdCents = amountCents;
    } else if (currency === "NIO" && rateScaled > 0n) {
        // amountCents / (rateScaled / 1e8), rounded half-up to USD cents.
        const numerator = amountCents * 100000000n;
        usdCents = (numerator + rateScaled / 2n) / rateScaled;
    }
    frm.set_value("amount_usd", Number(usdCents) / 100);
}

function toScaledInteger(value, decimalPlaces) {
    const raw = String(value || 0).trim();
    const match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?$/);
    if (!match) return 0n;
    const factor = 10n ** BigInt(decimalPlaces);
    const fraction = ((match[3] || "") + "0".repeat(decimalPlaces)).slice(0, decimalPlaces);
    const scaled = BigInt(match[2]) * factor + BigInt(fraction || "0");
    return match[1] === "-" ? -scaled : scaled;
}
