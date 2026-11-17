const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const schema = JSON.parse(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.json', 'utf8'));
const original = JSON.stringify(schema);
const context = {frappe: {provide() {}, ui: {form: {}}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('credinomina_reconciliation/public/js/period_quick_entry.js', 'utf8'), context);
assert.equal(context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm, undefined, 'Desk can load the asset before form.bundle');
context.frappe.ui.form.QuickEntryForm = class {
    set_meta_and_mandatory_fields() {
        this.docfields = schema.fields.filter(field => (field.reqd || field.allow_in_quick_entry) && !field.read_only);
    }
};
const dialog = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm();
dialog.set_meta_and_mandatory_fields();
const fields = Object.fromEntries(dialog.docfields.map(field => [field.fieldname, field]));
assert.equal(schema.quick_entry, 1);
for (const field of ['employer', 'payroll_month', 'reconciliation_mode', 'collection_cycle', 'cutoff_date', 'collection_file']) assert.ok(fields[field]);
assert.equal(fields.reconciliation_mode.reqd, 1);
assert.equal(fields.collection_file.fieldtype, 'Attach');
function evaluate(expression, doc) {
    return vm.runInNewContext(expression.slice(5), {doc});
}
for (const cycle of ['Mensual', 'Primera quincena', 'Segunda quincena', 'Fecha exacta']) {
    const doc = {reconciliation_mode: 'Operativa', collection_cycle: cycle};
    assert.equal(evaluate(fields.cutoff_date.depends_on, doc), cycle === 'Fecha exacta');
    assert.equal(evaluate(fields.cutoff_date.mandatory_depends_on, doc), cycle === 'Fecha exacta');
    assert.equal(evaluate(fields.cutoff_date.read_only_depends_on, doc), cycle !== 'Fecha exacta');
    assert.equal(evaluate(fields.collection_file.depends_on, doc), true);
}
const historical = {reconciliation_mode: 'Historica', collection_cycle: 'Fecha exacta'};
for (const name of ['collection_cycle', 'cutoff_date', 'collection_file']) assert.equal(evaluate(fields[name].depends_on, historical), false);
assert.equal(evaluate(fields.cutoff_date.mandatory_depends_on, historical), false);
assert.equal(JSON.stringify(schema), original, 'Quick Entry must not alter metadata used by the full form');
const hooks = fs.readFileSync('credinomina_reconciliation/hooks.py', 'utf8');
assert.ok(hooks.includes('/assets/credinomina_reconciliation/js/period_quick_entry.js'));
console.log('OK: period Quick Entry fields, exact-date requirements, historical visibility and isolated full-form metadata.');
