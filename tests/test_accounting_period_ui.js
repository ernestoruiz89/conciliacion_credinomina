const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const path = 'credinomina_reconciliation/conciliacion_credinomina/doctype/';
const schema = JSON.parse(fs.readFileSync(path + 'cn_reconciliation_period/cn_reconciliation_period.json', 'utf8'));
const dialogs = [], routes = [], calls = [];
const form = 'CN Reconciliation Period';
let defaults = {
    employer: 'A', payroll_month: '2026-09-01', reconciliation_mode: 'Operativa',
    collection_cycle: 'Mensual', cutoff_date: null,
};
const context = vm.createContext({__: text => text, frappe: {
    provide() {}, ui: {form: {on() {}, QuickEntryForm: class {
        constructor(doctype, _afterInsert, _initCallback, _doc, force) {
            this.doc = {doctype, name: 'draft-' + dialogs.length, __islocal: 1, employer: 'USER-DEFAULT'};
            this.force = force;
        }
        set_meta_and_mandatory_fields() {
            this.meta = schema;
            this.docfields = schema.fields.filter(field => field.reqd || field.allow_in_quick_entry);
        }
        async setup() {
            this.set_meta_and_mandatory_fields();
            dialogs.push(this);
        }
        update_doc() {}
    }}},
    model: {clear_doc() {}},
    async call(options) {
        calls.push(options);
        if (options.method.endsWith('accounting_period.get_period_defaults')) return {message: defaults};
        assert.ok(options.method.endsWith('period_quick_entry.create'));
        return {message: {doc: {name: 'P'}, source_import: options.args.source_link.name, movements_loaded: true}};
    },
    async set_route(...route) { routes.push(route); },
    msgprint() {},
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/public/js/period_quick_entry.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(path + 'cn_accounting_import/cn_accounting_import.js', 'utf8'), context);

function visible(dialog, fieldname) {
    const field = dialog.docfields.find(item => item.fieldname === fieldname);
    return vm.runInNewContext(field.depends_on.slice(5), {doc: dialog.doc});
}

(async () => {
    let saved = 0, reloaded = 0;
    const frm = {doc: {name: 'OLD'}, is_dirty: () => !saved, is_new: () => false,
        async save() {
            saved++;
            this.doc = {doctype: 'CN Accounting Import', name: 'I', modified: 'saved', bulk_event_date: '2026-09-15'};
        },
        async reload_doc() { reloaded++; },
    };
    const first = context.createAccountingPeriod(frm);
    await context.createAccountingPeriod(frm);
    await first;
    assert.equal(saved, 1);
    assert.equal(dialogs.length, 1);
    const dialog = dialogs[0];
    assert.equal(dialog.force, true, 'Open Quick Entry even when the full form is available');
    assert.equal(calls[0].args.import_name, 'I');
    assert.equal(dialog.doc.employer, 'A');
    assert.equal(dialog.doc.payroll_month, '2026-09-01');
    assert.equal(dialog.doc.reconciliation_mode, 'Operativa');
    assert.equal(dialog.doc.collection_cycle, 'Mensual', 'Use the suggested cycle for the company');
    assert.ok(visible(dialog, 'collection_file'));
    assert.ok(!visible(dialog, 'historical_scope'));
    assert.equal(routes.length, 0);
    assert.equal(calls.length, 1, 'Opening the modal does not create the period');

    defaults = {employer: 'B', payroll_month: '2026-08-01', reconciliation_mode: 'Historica',
        historical_scope: 'Fecha exacta', historical_application_date: '2026-08-15'};
    await context.createAccountingPeriod(frm);
    const historical = dialogs[1];
    assert.equal(historical.doc.reconciliation_mode, 'Historica');
    assert.equal(historical.doc.historical_scope, 'Fecha exacta');
    assert.equal(historical.doc.historical_application_date, '2026-08-15');
    assert.ok(visible(historical, 'historical_scope'));
    assert.ok(visible(historical, 'historical_application_date'));
    assert.ok(!visible(historical, 'historical_start_date'));
    assert.ok(!visible(historical, 'collection_file'));
    await historical.insert();
    assert.equal(calls[2].args.values.historical_application_date, '2026-08-15');
    assert.equal(calls[2].args.source_link.name, 'I');
    assert.equal(calls[2].args.source_link.modified, 'saved');
    assert.equal(reloaded, 1);
    assert.equal(routes.length, 0);
    assert.equal(frm._creating_period, false);

    context.frappe.call = async () => { throw new Error('Server unavailable'); };
    await assert.rejects(context.createAccountingPeriod(frm), /Server unavailable/);
    assert.equal(dialogs.length, 2);
    assert.equal(frm._creating_period, false);
    console.log('OK: Create period opens the prefilled modal, saves the historical date and reloads the import in place.');
})().catch(error => { console.error(error); process.exitCode = 1; });
