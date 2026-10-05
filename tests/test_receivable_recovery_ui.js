const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
let dialog, requests = [], messages = [], alerts = [], preview;
class Dialog {
    constructor(options) {
        this.options = options;
        this.values = Object.fromEntries(options.fields.map(field => [field.fieldname, field.default]));
        // Some Frappe controls call onchange while setting their initial value.
        options.fields.find(field => field.fieldname === 'employer').onchange();
        this.fields_dict = {cxc_info: {$wrapper: {html: value => {this.info = value;}}}};
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
            ? {message: await preview} : {message: {name: 'RECEIPT'}};}}});
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js'), 'utf8');
vm.runInContext(source.slice(0, source.indexOf('frappe.ui.form.on(')), context);
const companies = [{employer: 'A', available_usd: 10}, {employer: 'B', available_usd: 3}];
const form = () => ({doc: {name: 'ORIGIN'}, is_dirty: () => false,
    async save() {this.saved = true;}, async reload_doc() {this.reloaded = true;}});
(async () => {
    const frm = form(); preview = {companies, request_key: 'a'.repeat(32)};
    await context.cn_receivable_recovery_dialog(frm);
    assert.equal(dialog.shown, true);
    assert.equal(dialog.options.size, 'large');
    assert.ok(dialog.options.fields.some(field => field.fieldtype === 'Column Break'));
    assert.match(dialog.info, /10\.00/);
    dialog.values.deposit = 'OLD-DEPOSIT'; dialog.values.counterpart = 'OLD-COMP';
    dialog.values.employer = 'B';
    dialog.options.fields.find(field => field.fieldname === 'employer').onchange();
    assert.equal(dialog.values.amount_usd, 3);
    assert.ok(!dialog.values.deposit && !dialog.values.counterpart, 'Company change clears unrelated selections');
    assert.match(dialog.info, /3\.00/);
    const values = {employer: 'B', method: 'Depósito', deposit: 'D', amount_usd: 4,
        recovery_date: '2026-10-04', reason: 'Cobro'};
    await dialog.options.primary_action(values);
    assert.equal(requests.length, 1, 'Excess must be rejected before sending a mutation');
    values.amount_usd = 3;
    await dialog.options.primary_action(values);
    assert.equal(requests.at(-1).args.item_name, 'ORIGIN');
    assert.equal(requests.at(-1).args.destination, 'D');
    assert.equal(requests.at(-1).args.request_key, 'a'.repeat(32));
    assert.equal(dialog.hidden, true); assert.equal(dialog.disabled, false);
    assert.equal(frm.reloaded, true); assert.equal(alerts.length, 1);
    // A late preview or an already open modal must never write to another form.
    let resolve; preview = new Promise(done => {resolve = done;});
    const moved = form(); const pending = context.cn_receivable_recovery_dialog(moved);
    const oldDialog = dialog; moved.doc.name = 'OTHER'; resolve({companies, request_key: 'b'.repeat(32)});
    await pending;
    assert.equal(dialog, oldDialog, 'Stale preview must not open a dialog');
    preview = {companies, request_key: 'c'.repeat(32)};
    const open = form(); await context.cn_receivable_recovery_dialog(open);
    open.doc.name = 'OTHER'; const before = requests.length;
    await dialog.options.primary_action({...values, employer: 'A'});
    assert.equal(requests.length, before, 'An open dialog must not mutate a newly navigated form');
    // Network errors retain the selection and make the button usable again.
    const fresh = form(); await context.cn_receivable_recovery_dialog(fresh);
    context.frappe.call = async () => {throw new Error('Unavailable');};
    await assert.rejects(dialog.options.primary_action({...values, employer: 'A'}), /Unavailable/);
    assert.equal(dialog.disabled, false); assert.ok(!dialog.hidden);
    console.log('OK: CxC modal, layout, company scope, amount validation, confirmation, stale navigation and retry.');
})().catch(error => {console.error(error); process.exitCode = 1;});
