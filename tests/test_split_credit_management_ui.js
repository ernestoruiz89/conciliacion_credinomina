const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, release, reloads = 0;
const calls = [], messages = [];
const entry = {operation_id: 'PORTION', kind: 'Saldo a favor contra depósito', parent_document: 'DEP',
    client_name: 'CLIENTE', amount_usd: 25.22, management_history: [{operation_id: 'REFUND',
        fecha: '2025-12-01', tratamiento: 'Devolución', importe_usd: 5, referencia: 'COMPROBANTE'}]};
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {this.options = options; this.values = {}; dialog = this;}
        get_value(name) {return this.values[name];}
        set_df_property(name, property, value) {this.options.fields.find(f => f.fieldname === name)[property] = value;}
        get_primary_btn() {return {prop() {}};}
        show() {} hide() {this.hidden = true;}
    }},
    datetime: {get_today: () => '2026-10-28'},
    msgprint: message => messages.push(message), show_alert() {},
    call: async request => {
        if (request.method.endsWith('distribution_history')) return {message: {modified: 'v1', entries: [entry]}};
        calls.push(request);
        return new Promise(resolve => {release = resolve;});
    },
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), context);
const frm = {doc: {name: 'CORE'}, is_dirty: () => false, reload_doc: async () => {reloads++;}};

(async () => {
    await context.cn_manage_split_credit(frm);
    const fields = dialog.options.fields;
    for (const name of ['reference', 'treatment', 'amount_usd']) {
        const field = fields.find(f => f.fieldname === name);
        assert.equal(field.mandatory_depends_on, "eval:doc.action=='Documentar gestión'");
        assert.ok(!field.reqd, 'A reversal must not require refund fields');
    }
    const values = {credit: fields.find(f => f.fieldname === 'credit').options[1],
        action: 'Documentar gestión', treatment: 'Devolución', amount_usd: 1, event_date: '2026-10-28', notes: 'Devolución realizada'};
    for (const reference of [undefined, '', '   ']) {
        await dialog.options.primary_action({...values, reference});
    }
    assert.equal(calls.length, 0, 'An empty reference must never reach the server');
    assert.equal(messages.length, 3);
    assert.match(messages[0], /referencia del comprobante/);
    const pending = dialog.options.primary_action({...values, reference: '  DEV-001  '});
    await dialog.options.primary_action({...values, reference: 'DEV-001'});
    assert.equal(calls.length, 1, 'Double clicking cannot duplicate the refund');
    assert.ok(calls[0].method.endsWith('record_credit_management'));
    assert.equal(calls[0].args.reference, 'DEV-001');
    assert.equal(calls[0].args.operation_id, 'PORTION');
    assert.equal(calls[0].args.modified, 'v1');
    release({message: {pending_usd: 19.22}}); await pending;
    assert.equal(reloads, 1);
    assert.equal(dialog.hidden, true);

    await context.cn_manage_split_credit(frm);
    dialog.values.credit = dialog.options.fields.find(f => f.fieldname === 'credit').options[1];
    dialog.options.fields.find(f => f.fieldname === 'credit').onchange();
    const reversal = dialog.options.fields.find(f => f.fieldname === 'reversal').options[1];
    const reversing = dialog.options.primary_action({...values, credit: dialog.values.credit,
        action: 'Revertir gestión', reversal, notes: 'Corrección de comprobante'});
    assert.equal(calls.length, 2);
    assert.ok(calls[1].method.endsWith('reverse_credit_management'));
    assert.equal(calls[1].args.entry_id, 'REFUND');
    assert.equal(calls[1].args.reference, undefined);
    release({message: {pending_usd: 25.22}}); await reversing;
    assert.equal(reloads, 2);
    console.log('OK: required refund reference, single submission and reversal without refund fields.');
})().catch(error => {console.error(error); process.exitCode = 1;});
