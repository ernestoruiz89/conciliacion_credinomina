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
                    return copy;
                });
            }
        };
        Object.defineProperty(frappe.ui.form, "CNReconciliationPeriodQuickEntryForm", {
            value: Controller, writable: true, configurable: true,
        });
        return Controller;
    },
});
