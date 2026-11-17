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
    constructor(doc = {__islocal: 1}) { this.doc = doc; this.fields_dict = {reconciliation_mode: {}}; }
    set_meta_and_mandatory_fields() {
        this.docfields = schema.fields.filter(field => (field.reqd || field.allow_in_quick_entry) && !field.read_only);
    }
    get_value(field) {return this.doc[field];}
    set_value(field, value) {this.doc[field] = value;}
    update_doc() {}
    process_after_insert(response) {this.doc = response.message; this.processed = true;}
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
for (const [date, cycle] of [['2026-09-15', 'Primera quincena'], ['2026-09-30', 'Segunda quincena'],
        ['2026-10-31', 'Fecha exacta'], ['2026-10-16', 'Fecha exacta']]) {
    context.frappe._from_link = {field_obj: {frm: {doc: {doctype: 'CN Accounting Import', employer: 'E', bulk_event_date: date}}}};
    const quick = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm();
    quick.set_meta_and_mandatory_fields();
    assert.equal(quick.doc.employer, 'E');
    assert.equal(quick.doc.payroll_month, date.slice(0, 7) + '-01');
    assert.equal(quick.doc.collection_cycle, cycle);
    assert.equal(quick.doc.reconciliation_mode, 'Operativa');
    assert.equal(quick.doc.cutoff_date, cycle === 'Fecha exacta' ? date : undefined);
    const month = quick.docfields.find(field => field.fieldname === 'payroll_month');
    quick.doc.payroll_month = '2026-08-01';
    month.onchange();
    assert.equal(quick.doc.reconciliation_mode, 'Historica');
    quick.doc.payroll_month = '2026-09-01';
    month.onchange();
    assert.equal(quick.doc.reconciliation_mode, 'Operativa');
}
context.frappe._from_link = null;
const historicalQuick = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm({__islocal: 1, payroll_month: '2026-08-01'});
historicalQuick.set_meta_and_mandatory_fields();
assert.equal(historicalQuick.doc.reconciliation_mode, 'Historica');
const stored = {name: 'EXISTING', payroll_month: '2026-08-01', reconciliation_mode: 'Operativa'};
new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm(stored).set_meta_and_mandatory_fields();
assert.equal(stored.reconciliation_mode, 'Operativa', 'Defaults must not edit existing periods');
(async () => {
    const messages = [];
    context.__ = text => text;
    context.frappe.msgprint = message => messages.push(message);
    context.frappe.call = async options => {
        assert.ok(options.method.endsWith('period_quick_entry.create'));
        return {message: {doc: {name: 'CREATED'}, collection_loaded: true,
            reconciliation: {reviewed: 1, conforming: 1, differences: 0, missing_basis: 0}}};
    };
    const quick = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm();
    await quick.insert();
    assert.equal(quick.doc.name, 'CREATED');
    assert.equal(quick.processed, true);
    assert.equal(messages[0].indicator, 'green');
    let saved = false, reloaded = false;
    context.frappe._from_link = {field_obj: {df: {fieldname: 'historical_period'}, frm: {
        doc: {doctype: 'CN Accounting Import', name: 'I', modified: 'old', bulk_event_date: '2026-09-15'},
        is_new: () => false, is_dirty: () => true,
        async save() {saved = true; this.doc.modified = 'saved';},
        async reload_doc() {reloaded = true;},
    }}, doc: {doctype: 'CN Accounting Import'}};
    context.frappe.model = {clear_doc() {}};
    const linked = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm({__islocal: 1, collection_file: 'payroll.csv'});
    linked.set_meta_and_mandatory_fields();
    context.frappe.call = async options => {
        assert.ok(saved);
        assert.equal(options.args.source_link.name, 'I');
        assert.equal(options.args.source_link.modified, 'saved');
        return {message: {doc: {name: 'LINKED'}, collection_loaded: true, source_import: 'I'}};
    };
    await linked.insert();
    assert.equal(linked.doc.name, 'LINKED');
    assert.ok(reloaded);
    assert.equal(context.frappe._from_link, null);
    assert.ok(!linked.processed, 'The already-saved calling Link must not be dirtied again');
    context.frappe.call = async () => {throw new Error('Invalid payroll');};
    const failed = new context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm();
    failed.working = true;
    await assert.rejects(failed.insert(), /Invalid payroll/);
    assert.equal(failed.working, false);
    assert.ok(!failed.processed, 'Do not close or report success after a failed transaction');
    console.log('OK: period Quick Entry defaults, month boundary, exact-date requirements, automatic loading and error recovery.');
})().catch(error => {console.error(error); process.exitCode = 1;});
