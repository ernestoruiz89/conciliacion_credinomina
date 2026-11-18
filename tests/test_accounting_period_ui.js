const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const path = 'credinomina_reconciliation/conciliacion_credinomina/doctype/';
const schema = JSON.parse(fs.readFileSync(path + 'cn_reconciliation_period/cn_reconciliation_period.json', 'utf8'));
const dialogs = [], routes = [], calls = [];
const form = 'CN Reconciliation Period';
let formEvents, canCreate = true;
let defaults = {
    employer: 'A', payroll_month: '2026-09-01', reconciliation_mode: 'Operativa',
    collection_cycle: 'Mensual', cutoff_date: null,
};
const context = vm.createContext({__: text => text, frappe: {
    provide() {}, ui: {form: {on(_doctype, events) { if (events.refresh) formEvents = events; }, QuickEntryForm: class {
        constructor(_doctype, _afterInsert, _initCallback, doc, force) {
            assert.equal(doc.employer, defaults.employer);
            assert.equal(doc.payroll_month, defaults.payroll_month);
            assert.equal(doc.reconciliation_mode, defaults.reconciliation_mode);
            this.doc = doc;
            this.force = force;
        }
        set_meta_and_mandatory_fields() {
            this.meta = schema;
            this.docfields = schema.fields.filter(field => field.reqd || field.allow_in_quick_entry);
        }
        async setup() {
            this.set_meta_and_mandatory_fields();
            this.fields_list = this.docfields.map(df => ({df}));
            this.attach_doc_and_docfields(true);
            this.inputs = {}; // Rendering can reset a control independently of this.doc.
            dialogs.push(this);
        }
        attach_doc_and_docfields() {
            for (const field of this.fields_list) {
                field.df = schema.fields.find(df => df.fieldname === field.df.fieldname) || field.df;
            }
        }
        async set_values(values) {
            for (const field of this.docfields) {
                if (Object.hasOwn(values, field.fieldname)) this.inputs[field.fieldname] = values[field.fieldname];
            }
        }
        update_doc() { Object.assign(this.doc, this.inputs); }
    }}},
    model: {
        can_create(doctype) { assert.equal(doctype, form); return canCreate; },
        async with_doctype(doctype) { assert.equal(doctype, form); },
        get_new_doc(doctype) { return {doctype, name: 'draft-' + dialogs.length, __islocal: 1}; },
        clear_doc() {},
    },
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
    const buttons = new Map();
    const frm = {doc: {name: 'OLD'}, is_dirty: () => !saved, is_new: () => false,
        async save() {
            saved++;
            this.doc = {doctype: 'CN Accounting Import', name: 'I', modified: 'saved', bulk_event_date: '2026-09-15'};
        },
        async reload_doc() { reloaded++; },
        add_custom_button(label, action) { buttons.set(label, action); },
        remove_custom_button(label) { buttons.delete(label); },
    };
    formEvents.historical_period(frm);
    const create = buttons.get('Crear período');
    assert.deepEqual([...buttons.keys()], ['Crear período']);
    const first = create();
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
    assert.equal(dialog.inputs.employer, 'A');
    assert.equal(dialog.inputs.payroll_month, '2026-09-01');
    assert.equal(dialog.inputs.reconciliation_mode, 'Operativa');
    assert.equal(dialog.doc.collection_cycle, 'Mensual', 'Use the suggested cycle for the company');
    assert.ok(visible(dialog, 'collection_file'));
    assert.ok(dialog.docfields.some(field => field.fieldname === 'remark'));
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
    assert.equal(historical.inputs.employer, 'B');
    assert.equal(historical.inputs.payroll_month, '2026-08-01');
    assert.equal(historical.inputs.reconciliation_mode, 'Historica');
    assert.equal(historical.inputs.historical_application_date, '2026-08-15');
    const modeControl = historical.fields_list.find(field => field.df.fieldname === 'reconciliation_mode');
    assert.equal(modeControl.df.default, 'Historica', 'Refreshing controls must preserve the historical default');
    assert.equal(modeControl.doc, historical.doc);
    assert.ok(visible(historical, 'historical_scope'));
    assert.ok(visible(historical, 'historical_application_date'));
    assert.ok(!visible(historical, 'historical_start_date'));
    assert.ok(!visible(historical, 'collection_file'));
    await historical.set_values({remark: 'Segunda quincena de agosto'});
    await historical.insert();
    assert.equal(calls[2].args.values.remark, 'Segunda quincena de agosto');
    assert.equal(calls[2].args.values.historical_application_date, '2026-08-15');
    assert.equal(calls[2].args.source_link.name, 'I');
    assert.equal(calls[2].args.source_link.modified, 'saved');
    assert.equal(reloaded, 1);
    assert.equal(routes.length, 0);
    assert.equal(frm._creating_period, false);

    frm.doc.historical_period = 'PERIOD-1';
    canCreate = false;
    formEvents.historical_period(frm);
    assert.deepEqual([...buttons.keys()], ['Ver período']);
    await buttons.get('Ver período')();
    assert.deepEqual(routes[0], ['Form', form, 'PERIOD-1']);
    assert.equal(calls.length, 3, 'Viewing a linked period does not request draft defaults');
    frm.doc.historical_period = '';
    canCreate = true;
    formEvents.historical_period(frm);
    assert.deepEqual([...buttons.keys()], ['Crear período']);

    context.frappe.call = async () => { throw new Error('Server unavailable'); };
    await assert.rejects(context.createAccountingPeriod(frm), /Server unavailable/);
    assert.equal(dialogs.length, 2);
    assert.equal(frm._creating_period, false);
    console.log('OK: period modal controls receive defaults and the linked period opens from its own button.');
})().catch(error => { console.error(error); process.exitCode = 1; });
