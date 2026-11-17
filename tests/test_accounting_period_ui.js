const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const path = 'credinomina_reconciliation/conciliacion_credinomina/doctype/';
const schema = JSON.parse(fs.readFileSync(path + 'cn_reconciliation_period/cn_reconciliation_period.json', 'utf8'));
const drafts = [], routes = [], calls = [];
const form = 'CN Reconciliation Period';
let defaults = {
    employer: 'A', payroll_month: '2026-09-01', reconciliation_mode: 'Operativa',
    collection_cycle: 'Primera quincena', cutoff_date: null,
};
const context = vm.createContext({frappe: {
    ui: {form: {on() {}}},
    model: {
        async with_doctype(doctype) { assert.equal(doctype, form); },
        get_new_doc(doctype) {
            const draft = {doctype, name: 'new-period-' + drafts.length, __islocal: 1, employer: 'USER-DEFAULT'};
            drafts.push(draft);
            return draft;
        },
    },
    async call(options) {
        calls.push(options);
        assert.ok(options.method.endsWith('accounting_period.get_period_defaults'));
        return {message: defaults};
    },
    async set_route(...route) { routes.push(route); },
}});
vm.runInContext(fs.readFileSync(path + 'cn_accounting_import/cn_accounting_import.js', 'utf8'), context);
function visible(fieldname, doc) {
    const field = schema.fields.find(item => item.fieldname === fieldname);
    return vm.runInNewContext(field.depends_on.slice(5), {doc});
}
(async () => {
    let saved = 0;
    const frm = {doc: {name: 'OLD'}, is_dirty: () => !saved,
        async save() { saved++; this.doc = {name: 'I', modified: 'saved'}; }};
    const first = context.createAccountingPeriod(frm);
    await context.createAccountingPeriod(frm);
    await first;
    assert.equal(saved, 1);
    assert.equal(drafts.length, 1);
    const draft = drafts[0];
    assert.equal(calls[0].args.import_name, 'I');
    assert.equal(draft.employer, 'A');
    assert.equal(draft.payroll_month, '2026-09-01');
    assert.equal(draft.reconciliation_mode, 'Operativa');
    assert.ok(visible('files_section', draft));
    assert.equal(draft.__cn_accounting_source.name, 'I');
    assert.equal(draft.__cn_accounting_source.modified, 'saved');
    assert.deepEqual(routes[0], ['Form', form, draft.name]);

    defaults = {employer: 'B', payroll_month: '2026-08-01', reconciliation_mode: 'Historica',
        historical_scope: 'Fecha exacta', historical_application_date: '2026-08-15'};
    await context.createAccountingPeriod(frm);
    const historical = drafts[1];
    assert.equal(historical.reconciliation_mode, 'Historica');
    assert.equal(historical.historical_scope, 'Fecha exacta');
    assert.equal(historical.historical_application_date, '2026-08-15');
    assert.ok(visible('historical_scope', historical));
    assert.ok(visible('historical_application_date', historical));
    assert.ok(!visible('files_section', historical));
    assert.deepEqual(routes[1], ['Form', form, historical.name]);
    assert.equal(frm._creating_period, false);

    context.frappe.call = async () => { throw new Error('Server unavailable'); };
    await assert.rejects(context.createAccountingPeriod(frm), /Server unavailable/);
    assert.equal(drafts.length, 2);
    assert.equal(frm._creating_period, false);
    console.log('OK: Create period opens a prefilled, unsaved full form and carries its accounting source.');
})().catch(error => { console.error(error); process.exitCode = 1; });
