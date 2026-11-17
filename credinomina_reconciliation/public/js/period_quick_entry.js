frappe.provide("frappe.ui.form");
// Desk loads form.bundle lazily. Resolve the subclass only when Quick Entry
// selects its controller, after Frappe's base class is available.
Object.defineProperty(frappe.ui.form, "CNReconciliationPeriodQuickEntryForm", {
    configurable: true,
    get() {
        const Base = frappe.ui.form.QuickEntryForm;
        if (!Base) return undefined;
        const Controller = class extends Base {
            set_meta_and_mandatory_fields() {
                super.set_meta_and_mandatory_fields();
                const origin = frappe._from_link?.field_obj?.frm;
                this.accounting_source = origin?.doc?.doctype === "CN Accounting Import" ? origin : null;
                this.accounting_link = this.accounting_source && frappe._from_link?.field_obj?.df?.fieldname === "historical_period"
                    ? {row_name: frappe._from_link.doc?.doctype === "CN Source Row" ? frappe._from_link.doc.name : null} : null;
                const source = this.doc.__islocal ? this.accounting_source?.doc : null;
                const date = String(source?.bulk_event_date || "").slice(0, 10);
                this.source_date = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "";
                if (source && !this.doc.employer) this.doc.employer = source.employer;
                if (!this.doc.payroll_month && this.source_date) this.doc.payroll_month = this.source_date.slice(0, 7) + "-01";
                if (this.doc.__islocal && this.doc.payroll_month) this.doc.reconciliation_mode = this.mode_for_month(this.doc.payroll_month);
                if (!this.doc.collection_cycle && this.source_date) {
                    const day = this.source_date.slice(8);
                    this.doc.collection_cycle = day === "15" ? "Primera quincena" : day === "30" ? "Segunda quincena" : "Fecha exacta";
                }
                if (this.doc.collection_cycle === "Fecha exacta" && !this.doc.cutoff_date
                    && String(this.doc.payroll_month).slice(0, 7) === this.source_date.slice(0, 7)) {
                    this.doc.cutoff_date = this.source_date;
                }
                // Keep dialog rules local so the full form still shows
                // calculated monthly/quincenal cutoffs.
                this.docfields = this.docfields.map(field => {
                    const copy = {...field};
                    if (copy.fieldname === "cutoff_date") {
                        copy.depends_on = "eval:doc.reconciliation_mode != 'Historica' && doc.collection_cycle == 'Fecha exacta'";
                    }
                    if (copy.fieldname === "collection_file") {
                        copy.depends_on = "eval:doc.reconciliation_mode != 'Historica' && !doc.no_collection";
                    }
                    if (copy.fieldname === "payroll_month") {
                        const onchange = copy.onchange;
                        copy.onchange = () => {
                            onchange?.call(this.fields_dict?.payroll_month);
                            if (!this.doc.__islocal || !this.fields_dict?.reconciliation_mode) return;
                            const month = this.get_value("payroll_month");
                            if (month) this.set_value("reconciliation_mode", this.mode_for_month(month));
                            if (this.get_value("collection_cycle") === "Fecha exacta"
                                && String(this.get_value("cutoff_date") || "").slice(0, 7) !== String(month || "").slice(0, 7)) {
                                this.set_value("cutoff_date", this.source_date.slice(0, 7) === String(month || "").slice(0, 7) ? this.source_date : null);
                            }
                        };
                    }
                    return copy;
                });
            }

            mode_for_month(month) {
                return String(month).slice(0, 7) < "2026-09" ? "Historica" : "Operativa";
            }

            async insert() {
                if (!this.doc.__islocal) return super.insert();
                this.update_doc();
                try {
                    let sourceLink = null;
                    const load = this.doc.reconciliation_mode === "Operativa" && Boolean(this.doc.collection_file);
                    if (load && this.accounting_link) {
                        if (this.accounting_source.is_new()) frappe.throw(__("Guarde la importación antes de crear su período con cobranza."));
                        if (this.accounting_source.is_dirty()) await this.accounting_source.save();
                        sourceLink = {...this.accounting_link, name: this.accounting_source.doc.name, modified: this.accounting_source.doc.modified};
                    }
                    const {message: result} = await frappe.call({
                        method: "credinomina_reconciliation.period_quick_entry.create",
                        args: {values: this.doc, source_link: sourceLink}, freeze: true,
                        freeze_message: __(load ? "Guardando período, cargando cobranza y validando aplicaciones…" : "Guardando período…"),
                    });
                    if (result.source_import) {
                        // The calling Link is already saved on the server. Reload
                        // its rows as well so a later save cannot send stale links.
                        frappe.model.clear_doc(this.doc.doctype, this.doc.name);
                        this.doc = result.doc;
                        frappe._from_link = null;
                        try {
                            await this.accounting_source.reload_doc();
                        } catch (error) {
                            frappe.msgprint(__("Se guardaron el período y su vínculo, pero no se pudo actualizar la importación en pantalla. Recargue el formulario para consultar el resultado."));
                        }
                    } else {
                        this.process_after_insert({message: result.doc});
                    }
                    if (result.collection_loaded) {
                        const summary = result.reconciliation;
                        frappe.msgprint({title: __("Resultado de conciliación de cobranza"),
                            indicator: !summary || summary.differences || summary.missing_basis ? "orange" : "green",
                            message: summary
                                ? __("Cobranza cargada. Conciliación 1: {0} filas revisadas, {1} conformes, {2} con diferencias y {3} con base por revisar.",
                                    [summary.reviewed, summary.conforming, summary.differences, summary.missing_basis])
                                : __("Cobranza cargada. No hay movimientos del core disponibles para la conciliación 1; vincule las aplicaciones al período y vuelva a conciliar."),
                        });
                    }
                    return this.doc;
                } finally {
                    this.working = false;
                }
            }
        };
        Object.defineProperty(frappe.ui.form, "CNReconciliationPeriodQuickEntryForm", {
            value: Controller, writable: true, configurable: true,
        });
        return Controller;
    },
});
