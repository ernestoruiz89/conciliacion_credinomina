const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
let dialog, requests = [], messages = [], alerts = [], preview;
class Dialog {
    constructor(options) {
        this.options = options;
        this.values = Object.fromEntries(options.fields.map(field => [field.fieldname, field.default]));
        // Some Frappe controls call onchange while setting their initial value.
        options.fields.find(field => field.fieldname === 'employer').onchange();
        this.fields_dict = {cxc_info: {$wrapper: {html: value => {this.info = value;}}},
            destination_info: {$wrapper: {html: value => {this.destinationInfo = value;}}}};
        this.button = {prop: (key, value) => {this.disabled = value;}};
        dialog = this;
    }
    get_value(field) { return this.values[field]; }
    set_value(field, value) { this.values[field] = value; }
    get_primary_btn() { return this.button; }
    show() { this.shown = true; }
    hide() { this.hidden = true; }
}
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const context = vm.createContext({__: text => text, format_currency: value => `USD ${Number(value).toFixed(2)}`,
    frappe: {ui: {Dialog}, datetime: {get_today: () => '2026-10-04'}, utils: {escape_html: escape},
        msgprint: value => messages.push(value), show_alert: value => alerts.push(value),
        call: async args => {requests.push(args); return args.method.endsWith('preview_recovery')
            ? {message: await preview} : args.method.endsWith('get_recovery_destination')
                ? {message: {name: args.args.destination, available_usd: 7, receivable_usd: 3, suggested_usd: 3}}
                : {message: {name: 'RECEIPT'}};}}});
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js'), 'utf8');
vm.runInContext(source.slice(0, source.indexOf('frappe.ui.form.on(')), context);
const companies = [{employer: 'A', available_usd: 10}, {employer: 'B', available_usd: 3}];
const form = () => ({doc: {name: 'ORIGIN'}, is_dirty: () => false,
    async save() {this.saved = true;}, async reload_doc() {this.reloaded = true;}});
(async () => {
    const documentary = [];
    context.credinomina = {document_difference: async options => documentary.push(options)};
    const frm = form(); frm.doc.doctype = "CN Complementary Item";
    await context.cn_receivable_recovery_dialog(frm);
    assert.equal(documentary[0].name, "ORIGIN");
    assert.equal(documentary[0].doctype, "CN Complementary Item");
    assert.equal(requests.length, 0, "No receipt or synthetic cash is created");
    await documentary[0].on_saved();
    assert.equal(frm.reloaded, true);
    frm.is_dirty = () => true;
    await context.cn_receivable_recovery_dialog(frm);
    assert.equal(documentary.length, 1);
    assert.equal(frm.saved, undefined, "Opening documentation cannot save financial edits");
    console.log("OK: CxC documentation retains history without creating recovery movements.");
})().catch(error => {console.error(error); process.exitCode = 1;});
