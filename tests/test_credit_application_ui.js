const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, reloads = 0, pending, calls = [];
const context = vm.createContext({__: (text) => text, format_currency: value => `USD ${value}`, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {this.options = options; this.values = {}; dialog = this;
            for (const field of options.fields) this.values[field.fieldname] = field.default;}
        get_value(key) {return this.values[key];} set_value(key, value) {this.values[key] = value;}
        get_primary_btn() {return {prop() {}};} show() {} hide() {}
    }}, msgprint() {},
    async call(request) {calls.push(request);
        if (request.method.endsWith('.candidates')) return {message: {modified: 'SERVER', pending_usd: 20.77,
            rows: [{name: 'APP', event_date: '2026-05-30', period: 'MAY', pending_usd: 20.76, reference: 'ASIENTO'}]}};
        return new Promise(resolve => {pending = resolve;});
    },
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), context);
(async () => {
    const frm = {doc: {name: 'CREDIT', posting_date: '2026-04-30'}, is_dirty: () => false, reload_doc: async () => reloads++};
    await context.cn_apply_credit_to_application(frm);
    assert.equal(dialog.values.amount_usd, 20.76);
    assert.equal(dialog.values.event_date, '2026-05-30');
    assert.match(dialog.options.fields[0].options, /aunque su período esté cerrado/);
    const first = dialog.options.primary_action(dialog.values);
    await dialog.options.primary_action(dialog.values);
    assert.equal(calls.length, 2, 'Duplicate clicks must not submit twice');
    assert.equal(calls[1].args.application_id, 'APP');
    assert.equal(calls[1].args.modified, 'SERVER');
    pending({message: {pending_usd: .01, application_pending_usd: 0}});
    await first;
    assert.equal(reloads, 1);
    assert.equal(frm._cn_credit_management_busy, false);
    console.log('OK: imported application selection, partial amount, date, stale version and duplicate click protection.');
})().catch(error => {console.error(error); process.exitCode = 1;});
