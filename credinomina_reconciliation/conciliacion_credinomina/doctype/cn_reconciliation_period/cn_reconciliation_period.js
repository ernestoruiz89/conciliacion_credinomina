frappe.ui.form.on("CN Reconciliation Period", {
    refresh(frm) {
        setRemittanceDateEditing(frm);
        if (frm.is_new()) return;

        if (frm.doc.reconciliation_mode === "Historica") {
            if (frm.doc.status !== "Cerrado") {
                frm.add_custom_button(__("Cerrar período histórico"), () => {
                    frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.close_period",
                        args: { period_name: frm.doc.name },
                        freeze: true,
                    }).then(() => frm.reload_doc());
                });
            }
            return;
        }

        frm.add_custom_button(__("1. Cargar cobranza"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.import_collection",
                args: { period_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando cobranza y clientes..."),
            }).then(() => frm.reload_doc());
        });

        frm.add_custom_button(__("Exportar archivo empresa"), () => {
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.export_collection",
                args: { period_name: frm.doc.name },
            }).then((response) => {
                if (response.message?.file_url) window.open(response.message.file_url);
            });
        }, __("Más opciones"));

        if ((frm.doc.collection_rows || []).length) frm.add_custom_button(__("2. Cargar deducción de empresa"), async () => {
            if (frm.is_dirty()) await frm.save();
            frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.import_employer_response",
                args: { period_name: frm.doc.name },
                freeze: true,
                freeze_message: __("Cargando y comparando deducciones..."),
            }).then(() => frm.reload_doc());
        });

        if (frm.doc.deduction_basis === "Depósito coincidente" && frm.doc.status !== "Cerrado") {
            frm.add_custom_button(__("Revertir reconocimiento por depósito"), () => {
                frappe.confirm(
                    __("Se retirará la deducción inferida y el depósito volverá a quedar disponible. ¿Continuar?"),
                    () => frappe.call({
                        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.revert_deposit_recognition",
                        args: { period_name: frm.doc.name },
                        freeze: true,
                    }).then(() => frm.reload_doc())
                );
            }, __("Más opciones"));
        } else if (
            frm.doc.status !== "Cerrado" && !frm.doc.deduction_basis &&
            !frm.doc.employer_response_file && (frm.doc.collection_rows || []).length
        ) {
            frm.add_custom_button(__("Reconocer cobranza por depósito"), () => {
                showDepositRecognition(frm);
            }, __("Más opciones"));
        }

        if (frm.doc.status !== "Cerrado") {
            frm.add_custom_button(__("Cerrar periodo"), () => {
                frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.close_period",
                    args: { period_name: frm.doc.name },
                    freeze: true,
                }).then(() => frm.reload_doc());
            });
        }
    },
    collection_cycle(frm) {
        setRemittanceDateEditing(frm);
    },
    reconciliation_mode(frm) {
        setRemittanceDateEditing(frm);
    },
    historical_scope(frm) {
        if (frm.doc.historical_scope !== "Fecha exacta") {
            frm.set_value("historical_application_date", null);
        }
        if (frm.doc.historical_scope !== "Rango de fechas") {
            frm.set_value("historical_start_date", null);
            frm.set_value("historical_end_date", null);
        }
    },
    employer(frm) {
        if (!frm.is_new() || !frm.doc.employer || frm.doc.reconciliation_mode === "Historica") return;
        frappe.db.get_value("CN Employer", frm.doc.employer, "payroll_frequency").then((result) => {
            if (result.message?.payroll_frequency === "Mensual") {
                frm.set_value("collection_cycle", "Mensual");
            } else if (result.message?.payroll_frequency === "Quincenal" && frm.doc.collection_cycle === "Mensual") {
                frm.set_value("collection_cycle", "");
            }
        });
    },
});

function setRemittanceDateEditing(frm) {
    frm.set_df_property(
        "remittance_due_date", "read_only",
        frm.doc.reconciliation_mode === "Historica" ||
        !["Primera quincena", "Segunda quincena"].includes(frm.doc.collection_cycle)
    );
}

function showDepositRecognition(frm) {
    frappe.call({
        method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.get_recognizable_deposits",
        args: { period_name: frm.doc.name },
    }).then((response) => {
        const deposits = response.message || [];
        if (!deposits.length) {
            frappe.msgprint(__("No hay un depósito contable y bancario libre que coincida exactamente con la cobranza completa de este período."));
            return;
        }
        const labels = deposits.map((item) =>
            `${item.reference} · ${item.amount_usd} US$ · ${item.event_date} · ${item.source_row_id}`
        );
        const dialog = new frappe.ui.Dialog({
            title: __("Reconocer deducción por depósito coincidente"),
            fields: [
                {
                    fieldname: "warning", fieldtype: "HTML",
                    options: `<p>${__("Esta deducción será inferida del depósito, no confirmada por un detalle de planilla. El estado de cuenta mantendrá esa distinción.")}</p>`,
                },
                { fieldname: "deposit", fieldtype: "Select", label: __("Depósito"), options: labels.join("\n"), reqd: 1 },
                { fieldname: "justification", fieldtype: "Small Text", label: __("Justificación y soporte"), reqd: 1 },
            ],
            primary_action_label: __("Reconocer cobranza"),
            primary_action(values) {
                const index = labels.indexOf(values.deposit);
                if (index < 0) return;
                frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period.recognize_collection_from_deposit",
                    args: {
                        period_name: frm.doc.name,
                        source_row_id: deposits[index].source_row_id,
                        justification: values.justification,
                    },
                    freeze: true,
                }).then(() => {
                    dialog.hide();
                    frm.reload_doc();
                });
            },
        });
        dialog.show();
    });
}
