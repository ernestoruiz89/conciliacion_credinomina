const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, choose, saved;
const rows = [{name: 'A', client_name: 'Cliente', adjustable_usd: 26.01, protected_usd: 111.32}];
const context = vm.createContext({window: {}, __: x => x, format_currency: String, setTimeout,
    frappe: {ui: {form: {on() {}}, Dialog: class {
        constructor(options) {dialog = this; this.options = options; this.fields_dict = {};
            for (const field of options.fields) if (field.fieldname) this.fields_dict[field.fieldname] = {df: field};
            this.fields_dict.distribution_summary.$wrapper = {html() {}};
            this.fields_dict.applications.grid = {refresh() {}, wrapper: {on: (_e, _s, cb) => choose = cb}};
        }
        get_value() {return 'IMPORT';} show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
    }}, call: async args => {if (args.method.endsWith('application_candidates')) return {message: rows};
        saved = JSON.parse(args.args.rows); return {};}, msgprint() {}, show_alert() {}}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), context);
(async () => {
    const frm = {doc: {name: 'ITEM', employer: 'E', amount_usd: 100, docstatus: 0},
        is_dirty: () => false, reload_doc: async () => {}};
    await context.cn_distribute_applications(frm);
    await dialog.options.fields.find(field => field.fieldname === 'accounting_import').onchange();
    const data = dialog.fields_dict.applications.df.data;
    data[0].__checked = 1; choose(); await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(data[0].amount_usd, 26.01);
    await dialog.options.primary_action();
    assert.deepEqual(saved, [{application: 'A', amount_usd: 26.01}], 'Protected deposits never become adjustable');
    assert.equal(frm.doc.related_application, undefined, 'Existing form links change only through server distribution');
    console.log('OK: mixed settlement checkbox respects the available balance after protected cash.');
})().catch(error => {console.error(error); process.exitCode = 1;});
