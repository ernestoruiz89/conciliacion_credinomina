const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, summary = '', candidates = [], requests = [], saved;
const messages = [], handlers = {};
const context = vm.createContext({window: {}, __: x => x, format_currency: n => '$ ' + Number(n).toFixed(2), setTimeout,
    frappe: {utils: {escape_html: String}, ui: {form: {on: (dt, h) => handlers[dt] = h}, Dialog: class {
        constructor(options) {
            dialog = this; this.options = options; this.values = {}; this.fields_dict = {};
            for (const field of options.fields) if (field.fieldname) this.fields_dict[field.fieldname] = {df: field};
            this.fields_dict.distribution_summary.$wrapper = {html: value => summary = value};
            this.fields_dict.applications.grid = {refresh() {}, wrapper: {on: (_e, _s, action) => this.check = action}};
            this.fields_dict.client_search.$input = {on: (_e, action) => this.search = action, val: () => this.values.client_search};
        }
        get_value(name) {return this.values[name];}
        show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
    }}, msgprint: msg => messages.push(msg), show_alert() {},
    call: async args => {
        requests.push(args);
        if (args.method.endsWith('application_candidates')) return {message: candidates};
        saved = JSON.parse(args.args.rows); return {};
    }}});
const code = fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8');
vm.runInContext(code, context);
const frm = {doc: {name: 'ITEM', employer: 'EMP', amount_usd: 100, docstatus: 1,
    related_application: 'EXISTING', application_adjustment_usd: 20}, is_dirty: () => false, reload_doc: async () => {}};
const rows = () => dialog.fields_dict.applications.df.data;
const check = async () => {dialog.check(); await new Promise(resolve => setTimeout(resolve, 5));};
const amounts = () => Array.from(rows(), row => row.amount_usd);
const load = async name => {dialog.values.accounting_import = name;
    await dialog.options.fields.find(f => f.fieldname === 'accounting_import').onchange();};
const search = value => {dialog.values.client_search = value; dialog.search();};
const candidate = (name, available) => ({name, client_name: name, adjustable_usd: available});
(async () => {
    assert.equal(context.cn_select_original_application, undefined, 'Obsolete single-link modal was removed');
    const before = JSON.stringify(frm.doc);
    await context.cn_distribute_applications(frm);
    assert.ok(summary.includes('Remanente disponible') && summary.includes('$ 80.00'));
    assert.ok(summary.includes('Seleccionado') && summary.includes('Por distribuir'));
    candidates = [candidate('Ana', 40), candidate('José', 60), candidate('Carlos', 50)];
    await load('FIRST');
    rows()[0].__checked = 1; await check();
    assert.deepEqual(amounts(), [40, 0, 0]);
    rows()[1].__checked = 1; await check();
    assert.deepEqual(amounts(), [40, 40, 0], 'Second application receives only the item remainder');
    rows()[2].__checked = 1; await check();
    assert.deepEqual(amounts(), [40, 40, 0]);
    assert.equal(rows()[2].__checked, 0, 'No checked zero-amount selection when cash is exhausted');
    rows()[0].__checked = 0; rows()[2].__checked = 1; await check();
    assert.deepEqual(amounts(), [0, 40, 40], 'Unchecking releases the amount before new assignments');
    const amountField = dialog.fields_dict.applications.df.fields.find(f => f.fieldname === 'amount_usd');
    rows()[1].amount_usd = 1000; amountField.onchange.call({doc: rows()[1]});
    assert.equal(rows()[1].amount_usd, 40, 'Manual edits also respect the item remainder');
    rows()[1].amount_usd = 20; amountField.onchange.call({doc: rows()[1]});
    assert.ok(summary.includes('$ 60.00') && summary.includes('$ 20.00'));
    search('carlos');
    assert.equal(rows().length, 1); assert.equal(rows()[0].__checked, 1);
    rows()[0].__checked = 0; await check();
    assert.ok(summary.includes('$ 20.00') && summary.includes('$ 60.00'), 'Filtered-out selections remain in totals');
    candidates = [candidate('Beatriz', 90)];
    await load('SECOND'); search('');
    rows()[0].__checked = 1; await check();
    assert.equal(rows()[0].amount_usd, 60, 'A second import shares the same remaining item capacity');
    await dialog.options.primary_action();
    assert.deepEqual(saved, [{application: 'José', amount_usd: 20}, {application: 'Beatriz', amount_usd: 60}]);
    assert.equal(JSON.stringify(frm.doc), before, 'Selection never rewrites existing confirmed links');
    frm.doc.docstatus = 0;
    await context.cn_distribute_applications(frm);
    assert.ok(summary.includes('$ 100.00') && summary.includes('$ 20.00') && summary.includes('$ 80.00'));
    candidates = [candidate('EXISTING', 30), candidate('NEW', 80)];
    await load('DRAFT');
    assert.equal(rows()[0].__checked, 1); assert.equal(rows()[0].amount_usd, 20);
    rows().forEach(row => row.__checked = 1); await check();
    assert.deepEqual(amounts(), [20, 80], 'Select-all keeps prior partial amounts and uses cents safely');
    rows().forEach(row => row.__checked = 0); await check();
    assert.deepEqual(amounts(), [0, 0]);
    console.log('OK: checkboxes, manual amounts, cross-import totals, filtering, draft links, select-all and confirmed links.');
})().catch(error => {console.error(error); process.exitCode = 1;});
