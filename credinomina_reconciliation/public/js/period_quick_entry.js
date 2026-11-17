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
                const origin = this.accounting_source || frappe._from_link?.field_obj?.frm;
                this.accounting_source = origin?.doc?.doctype === "CN Accounting Import" ? origin : null;
                this.accounting_link = this.accounting_link || (this.accounting_source && frappe._from_link?.field_obj?.df?.fieldname === "historical_period"
                    ? {row_name: frappe._from_link.doc?.doctype === "CN Source Row" ? frappe._from_link.doc.name : null} : null);
                const source = this.doc.__islocal ? this.accounting_source?.doc : null;
                this.source_date = this.application_date(source);
                const defaults = this.doc.__islocal && this.accounting_defaults;
                const historicalFields = ["historical_scope", "historical_application_date", "historical_start_date", "historical_end_date"];
                if (defaults) {
                    const fields = new Set([...this.docfields.map(field => field.fieldname),
                        ...historicalFields, "collection_cycle", "cutoff_date", "collection_file"]);
                    this.docfields = this.meta.fields.filter(field => fields.has(field.fieldname) && !field.read_only);
                } else if (source) {
                    this.doc.employer = source.employer;
                    if (this.source_date) this.doc.payroll_month = this.source_date.slice(0, 7) + "-01";
                }
                if (this.doc.__islocal && this.doc.payroll_month) this.doc.reconciliation_mode = this.mode_for_month(this.doc.payroll_month);
                if (!defaults && !this.doc.collection_cycle && this.source_date) {
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
                    // Controls apply their defaults while the dialog is built;
                    // seed them too, so rendering cannot replace source values.
                    if (this.doc.__islocal && this.doc[copy.fieldname] != null) copy.default = this.doc[copy.fieldname];
                    if (copy.fieldname === "cutoff_date") {
                        copy.depends_on = "eval:doc.reconciliation_mode != 'Historica' && doc.collection_cycle == 'Fecha exacta'";
                    }
                    if (copy.fieldname === "collection_file") {
                        copy.depends_on = "eval:doc.reconciliation_mode == 'Operativa' && !doc.no_collection";
                    }
                    if (historicalFields.includes(copy.fieldname) && copy.fieldtype === "Date") {
                        copy.mandatory_depends_on = copy.depends_on;
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
                const fileIndex = this.docfields.findIndex(field => field.fieldname === "collection_file");
                if (fileIndex >= 0) {
                    this.docfields.splice(fileIndex, 0, {
                        fieldname: "download_collection_template", fieldtype: "Button",
                        label: __("Descargar plantilla de cobranza"),
                        depends_on: "eval:doc.reconciliation_mode == 'Operativa' && !doc.no_collection",
                        click: () => {
                            if (this.get_value("reconciliation_mode") !== "Operativa" || this.doc.no_collection) return;
                            window.open("/api/method/credinomina_reconciliation.template_download.download_import_template?template_type=cobranza", "_blank", "noopener");
                        },
                    });
                }
            }

            attach_doc_and_docfields(refresh) {
                if (!this.accounting_defaults) return super.attach_doc_and_docfields(refresh);

                // The generic layout replaces each control's definition with
                // the DocType default, losing this dialog's seeded values and rules.
                for (const field of this.fields_list) {
                    field.doc = this.doc;
                    field.doctype = this.doc.doctype;
                    field.docname = this.doc.name;
                    if (refresh) field.refresh?.();
                }
            }

            application_date(source) {
                const valid = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value || "").slice(0, 10));
                if (valid(source?.bulk_event_date)) return String(source.bulk_event_date).slice(0, 10);
                return (source?.rows || []).reduce((earliest, row) => {
                    if (row.event_type !== "Aplicacion" || !valid(row.event_date)) return earliest;
                    const date = String(row.event_date).slice(0, 10);
                    return !earliest || date < earliest ? date : earliest;
                }, "");
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
                    if (this.accounting_link) {
                        if (this.accounting_source.is_new()) frappe.throw(__("Guarde la importación antes de crear su período."));
                        if (this.accounting_source.is_dirty()) await this.accounting_source.save();
                        sourceLink = {...this.accounting_link, name: this.accounting_source.doc.name, modified: this.accounting_source.doc.modified};
                    }
                    const {message: result} = await frappe.call({
                        method: "credinomina_reconciliation.period_quick_entry.create",
                        args: {values: {...this.doc, collection_file: load ? this.doc.collection_file : null}, source_link: sourceLink}, freeze: true,
                        freeze_message: __(load ? "Guardando período, cargando cobranza y movimientos contables…"
                            : sourceLink ? "Guardando período y cargando movimientos contables…" : "Guardando período…"),
                    });
                    if (result.source_import) {
                        // The calling Link is already saved on the server. Reload
                        // its rows as well so a later save cannot send stale links.
                        frappe.model.clear_doc(this.doc.doctype, this.doc.name);
                        this.doc = result.doc;
                        frappe._from_link = null;
                        try {
                            if (result.source_import !== this.accounting_source.doc.name) {
                                await frappe.set_route("Form", "CN Accounting Import", result.source_import);
                            } else {
                                await this.accounting_source.reload_doc();
                            }
                        } catch (error) {
                            frappe.msgprint(__("Se guardaron el período y su vínculo, pero no se pudo actualizar la importación en pantalla. Vuelva a abrir la importación para consultar el resultado."));
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
                    } else if (result.movements_loaded) {
                        frappe.msgprint({title: __("Período vinculado"), indicator: "green",
                            message: __("El período quedó guardado en la importación. Se cargaron {0} movimientos contables, conservando las asignaciones existentes.", [result.movement_rows]),
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
