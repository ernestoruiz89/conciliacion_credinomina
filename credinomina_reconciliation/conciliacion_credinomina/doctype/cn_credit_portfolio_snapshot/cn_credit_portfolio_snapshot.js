frappe.ui.form.on("CN Credit Portfolio Snapshot", {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__("Importar / actualizar corte"), () => {
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot.cn_credit_portfolio_snapshot.import_portfolio_snapshot",
                args: { snapshot_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Importando créditos y verificando clientes y empresas..."),
            }).then(() => frm.reload_doc());
        });
    },
});
