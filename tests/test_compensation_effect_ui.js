const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
let dialog, calls = [], reloaded = 0;
const data = {credit_item: 'C', operations: [{operation_id: 'OFFSET', counterpart: 'CORE', amount_usd: 19.25, credit_effect: 'Consume saldo'}],
    management: [{entry_id: 'REFUND', treatment: 'Devolución', date: '2025-08-20', reference: '001011316', amount_usd: 19.25}]};
const context = vm.createContext({__: text => text, format_currency: value => `USD ${value}`, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {
            Object.assign(this, options); this.values = {}; this.fields_dict = {};
            for (const field of this.fields) { this.values[field.fieldname] = field.default; this.fields_dict[field.fieldname] = {df: field, $wrapper: {empty() {}, html() {}}}; }
            dialog = this;
        }
        show() {} hide() {} get_primary_btn() { return {prop() {}}; }
        get_value(key) { return this.values[key]; }
        async set_value(key, value) { this.values[key] = value; }
        set_df_property(key, prop, value) { this.fields_dict[key].df[prop] = value; }
    }}, datetime: {str_to_user: value => value, get_today: () => '2026-10-15'},
    utils: {escape_html: String}, msgprint() {}, show_alert() {},
    call: async args => {
        calls.push(args);
        if (args.method.endsWith('.options')) return {message: data};
        if (args.method.endsWith('.preview')) return {message: {token: 'TOKEN', credit_pending_usd: 0}};
        if (args.method.endsWith('preview_compensation')) return {message: {request_key: 'REQUEST', suggested_usd: 19.25,
            left: {name: 'C', is_credit: true, management_pending_usd: 0}, right: {name: 'CORE'}}};
        return {message: {}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js'), 'utf8'), context);
const frm = {doc: {name: 'C', docstatus: 1, category: 'Saldo a favor del cliente'}, is_dirty: () => false, reload_doc: async () => {reloaded++;}};
(async () => {
    await context.cn_review_compensation_effect(frm);
    let first = dialog;
    await first.primary_action({operation: first.fields_dict.operation.df.options[0], effect: 'Vínculo de gestión', management: first.fields_dict.management.df.options[1]});
    assert.equal(calls.at(-1).args.credit_management_entry, 'REFUND');
    await dialog.primary_action({reason: 'Comprobante verificado'});
    assert.equal(calls.at(-1).args.token, 'TOKEN');
    assert.equal(calls.at(-1).args.operation_id, 'OFFSET');
    assert.equal(calls.at(-1).args.credit_effect, 'Vínculo de gestión');
    await context.cn_compensate_items_dialog(frm);
    first = dialog;
    await first.set_value('counterpart', 'CORE');
    await first.fields_dict.counterpart.df.onchange();
    await first.set_value('credit_effect', 'Vínculo de gestión');
    await first.set_value('credit_management_entry', first.fields_dict.credit_management_entry.df.options[1]);
    await first.fields_dict.credit_management_entry.df.onchange();
    assert.equal(calls.at(-1).args.credit_management_entry, 'REFUND');
    await first.primary_action({...first.values, reviewed: 1, compensation_date: '2025-11-21', reason: 'Respaldo contable'});
    const confirmed = calls.findLast(row => row.method.endsWith('confirm_compensation'));
    assert.equal(confirmed.args.credit_effect, 'Vínculo de gestión');
    assert.equal(confirmed.args.credit_management_entry, 'REFUND');
    assert.equal(confirmed.args.request_key, 'REQUEST');
    assert.equal(reloaded, 2);
    console.log('OK: explicit management identity for new and existing offsets, preview and confirmation.');
})().catch(error => {console.error(error); process.exitCode = 1;});
