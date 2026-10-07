const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const calls = [], routes = [], notices = [];
let dialog;
const defaults = {employer: "A", payroll_month: "2025-04-01", payroll_frequency: "Quincenal",
    historical_scope: "Fecha exacta", historical_application_date: "2025-04-15"};
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {
            dialog = this; this.options = options; this.values = {}; this.properties = {};
            for (const field of options.fields) {
                this.values[field.fieldname] = field.default;
                if (field.onchange) field.onchange(); // Construction must not access an uninitialized dialog.
            }
        }
        get_value(field) {return this.values[field];}
        set_df_property(field, property, value) {(this.properties[field] ||= {})[property] = value;}
        show() {this.shown = true;} hide() {this.shown = false;}
    }},
    call: async args => {calls.push(args); return {message: args.method.endsWith("get_period_defaults") ? defaults : {name: "P", status: "Borrador"}};},
    show_alert: value => notices.push(value), set_route: (...args) => routes.push(args),
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_accounting_import/cn_accounting_import.js"), "utf8"), context);
(async () => {
    let saved = false;
    const frm = {doc: {name: "OLD"}, is_dirty: () => true, save: async () => {saved = true; frm.doc.name = "I";}};
    await context.createAccountingPeriod(frm);
    assert.ok(saved && dialog.shown);
    assert.equal(calls[0].args.import_name, "I");
    assert.equal(dialog.properties.historical_application_date.reqd, true);
    assert.equal(dialog.properties.collection_cycle.hidden, true);
    const fields = Object.fromEntries(dialog.options.fields.map(field => [field.fieldname, field]));
    dialog.values.payroll_month = "2026-09-01";
    fields.payroll_month.onchange();
    assert.equal(dialog.properties.historical_application_date.reqd, false);
    assert.equal(dialog.properties.collection_cycle.reqd, true);
    assert.equal(fields.collection_cycle.default, "", "Do not guess a quincena");
    assert.ok(fields.collection_cycle.options.includes("Fecha exacta"));
    dialog.values.collection_cycle = "Fecha exacta";
    fields.collection_cycle.onchange();
    assert.equal(dialog.properties.cutoff_date.hidden, false);
    assert.equal(dialog.properties.cutoff_date.reqd, true);
    dialog.values.collection_cycle = "Primera quincena";
    fields.collection_cycle.onchange();
    assert.equal(dialog.properties.cutoff_date.reqd, false);
    assert.equal(dialog.properties.cutoff_date.hidden, true);
    const values = {payroll_month: "2026-09-01", collection_cycle: "Primera quincena"};
    const creating = dialog.options.primary_action(values);
    await dialog.options.primary_action(values);
    await creating;
    assert.equal(calls.length, 2, "Block double-click creation");
    assert.equal(calls[1].args.values, values);
    assert.deepEqual(routes[0], ["Form", "CN Reconciliation Period", "P"]);
    assert.equal(notices[0].message, "Período creado en Borrador");
    assert.equal(dialog.shown, false);
    assert.equal(frm._creating_period, false);
    console.log("OK: save before defaults, historical/operative fields, blank quincena, single draft creation and navigation.");
})().catch(error => {console.error(error); process.exitCode = 1;});
