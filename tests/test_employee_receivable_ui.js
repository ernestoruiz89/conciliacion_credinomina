const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.join(__dirname, '../credinomina_reconciliation/conciliacion_credinomina/doctype');
const handlers = {}, requests = [];
let dialog;
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on: (dt, events) => handlers[dt] = events}, Dialog: class {
        constructor(options) { this.options = options; this.values = {}; dialog = this; }
        show() {} get_value(key) { return this.values[key]; }
        async set_value(key, value) { this.values[key] = value; }
    }}, model: {can_create: () => true, can_submit: () => true}, session: {user: 'Operator'},
    datetime: {get_today: () => '2026-10-13'},
    db: {get_value: async (dt) => ({message: dt === 'CN Complementary Subcategory'
        ? {effect: 'CxC al Empleado'} : {client_number: '123'}})},
    call: async args => {requests.push(args); return {message: {due_date: '2025-05-10'}};},
}});
for (const name of ['cn_complementary_item', 'cn_remittance_allocation'])
    vm.runInContext(fs.readFileSync(path.join(root, name, name + '.js'), 'utf8'), context);
const document = {category: 'Ajuste de conciliación', subcategory_effect: 'CxC al Empleado'};
const schema = JSON.parse(fs.readFileSync(path.join(root, 'cn_complementary_item/cn_complementary_item.json'), 'utf8'));
const enabled = expression => vm.runInNewContext(expression.slice(5), {doc: document});
for (const key of ['credit_client', 'credit_assigned_to', 'credit_commitment_date']) {
    const field = schema.fields.find(row => row.fieldname === key);
    assert.equal(enabled(field.mandatory_depends_on), true, `${key} must be required for the debtor`);
}
assert.equal(enabled(schema.fields.find(row => row.fieldname === 'receivable_terms_section').depends_on), true);
assert.equal(enabled(schema.fields.find(row => row.fieldname === 'select_credit_detail').depends_on), false);
(async () => {
    const documentary = [];
    context.credinomina = {document_difference: async options => documentary.push(options)};
    await context.createRemittanceComplementary({doc: {doctype: "CN Remittance Allocation", name: "D"},
        is_new: () => false, is_dirty: () => false});
    assert.equal(documentary[0].name, "D");
    assert.equal(documentary[0].parentfield, "");
    assert.equal(requests.length, 0, "Documentary shortcut never creates a manual CxC");
    console.log("OK: existing CxC metadata retained; new differences are documentary.");
})().catch(error => { console.error(error); process.exitCode = 1; });
