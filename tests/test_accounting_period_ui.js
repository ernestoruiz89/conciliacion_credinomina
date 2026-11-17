const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const path = 'credinomina_reconciliation/conciliacion_credinomina/doctype/';
const schema = JSON.parse(fs.readFileSync(path + 'cn_reconciliation_period/cn_reconciliation_period.json', 'utf8'));
const opened = [], calls = [], routes = [], notices = [];
let failSetup = false;
const context = vm.createContext({__: text => text, frappe: {
    provide() {}, ui: {form: {on() {}, QuickEntryForm: class {
        constructor(doctype, after_insert, init, doc, force) {
            this.doc = doc || {doctype, name: 'LOCAL', __islocal: 1};
            this.after_insert = after_insert; this.force = force;
            this.fields_dict = {reconciliation_mode: {}};
            opened.push(this);
        }
        async setup() {
            if (failSetup) throw new Error('Failed metadata');
            this.set_meta_and_mandatory_fields(); this.shown = true; return this;
        }
        set_meta_and_mandatory_fields() {
            this.docfields = schema.fields.filter(field => (field.reqd || field.allow_in_quick_entry) && !field.read_only);
        }
        get_value(field) {return this.doc[field];}
        set_value(field, value) {this.doc[field] = value;}
        update_doc() {}
        process_after_insert(response) {this.doc = response.message; this.after_insert?.(this.doc);}
    }}},
    model: {clear_doc() {}},
    call: async options => {
        calls.push(options);
        return {message: {doc: {name: 'P'}, source_import: options.args.source_link?.name,
            collection_loaded: Boolean(options.args.values.collection_file),
            reconciliation: {reviewed: 1, conforming: 1, differences: 0, missing_basis: 0}}};
    },
    msgprint: value => notices.push(value), set_route: (...args) => routes.push(args),
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/public/js/period_quick_entry.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(path + 'cn_accounting_import/cn_accounting_import.js', 'utf8'), context);
(async () => {
    let saved = 0, reloaded = 0;
    const frm = {doc: {doctype: 'CN Accounting Import', name: 'OLD', employer: 'A', bulk_event_date: '2026-09-15'},
        is_new: () => false, is_dirty: () => !saved,
        save: async () => {saved++; frm.doc.name = 'I'; frm.doc.modified = 'saved';},
        reload_doc: async () => {reloaded++;},
    };
    const first = context.createAccountingPeriod(frm);
    await context.createAccountingPeriod(frm); // Duplicate click while saving.
    await first;
    assert.equal(opened.length, 1);
    const dialog = opened[0];
    assert.ok(dialog instanceof context.frappe.ui.form.CNReconciliationPeriodQuickEntryForm);
    assert.equal(saved, 1);
    assert.ok(dialog.shown && dialog.force);
    assert.equal(context.frappe.quick_entry, dialog);
    assert.equal(dialog.accounting_source, frm);
    assert.equal(dialog.doc.employer, 'A');
    assert.equal(dialog.doc.payroll_month, '2026-09-01');
    assert.equal(dialog.doc.reconciliation_mode, 'Operativa');
    assert.equal(dialog.doc.collection_cycle, 'Primera quincena');
    assert.ok(dialog.docfields.some(field => field.fieldname === 'collection_file'));
    assert.equal(calls.length, 0, 'Opening the dialog must not create or reconcile anything');
    dialog.doc.collection_file = 'payroll.csv';
    await dialog.insert();
    assert.equal(calls.length, 1);
    assert.ok(calls[0].method.endsWith('period_quick_entry.create'));
    assert.equal(calls[0].args.source_link.name, 'I');
    assert.equal(calls[0].args.source_link.modified, 'saved');
    assert.equal(calls[0].args.source_link.row_name, null);
    assert.equal(reloaded, 1);
    assert.equal(notices[0].indicator, 'green');
    assert.equal(frm._creating_period, false);

    frm.doc.bulk_event_date = '2025-04-16';
    await context.createAccountingPeriod(frm);
    const historical = opened[1];
    assert.equal(historical.doc.reconciliation_mode, 'Historica');
    await historical.insert();
    assert.equal(calls[1].args.source_link, null);
    assert.deepEqual(routes[0], ['Form', 'CN Reconciliation Period', 'P']);
    failSetup = true;
    await assert.rejects(context.createAccountingPeriod(frm), /Failed metadata/);
    assert.equal(frm._creating_period, false);
    assert.equal(context.frappe.quick_entry, null);
    console.log('OK: Create period uses shared Quick Entry, passes defaults/context, saves before opening, preserves automatic reconciliation and recovers from setup errors.');
})().catch(error => {console.error(error); process.exitCode = 1;});
