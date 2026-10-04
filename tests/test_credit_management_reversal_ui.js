const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let dialog;
const calls = [];
let reloaded = 0;
const context = vm.createContext({
    __: text => text,
    format_currency: value => `USD ${Number(value).toFixed(2)}`,
    frappe: {
        ui: {form: {on() {}}, Dialog: class {
            constructor(options) { this.options = options; dialog = this; }
            show() {} hide() { this.hidden = true; }
            get_primary_btn() { return {prop() {}}; }
        }},
        datetime: {get_today: () => '2026-10-03', str_to_user: value => `formatted-${value}`},
        call: async args => {
            calls.push(args);
            if (args.method.endsWith('get_management_history')) return {message: {
                modified: 'server-version', rows: [
                    {entry_id: 'ACTIVE', can_reverse: true, tratamiento: 'Devolución', importe_usd: 4, fecha: '2025-05-10'},
                    {entry_id: 'REVERSED', can_reverse: false, tratamiento: 'Devolución', importe_usd: 6},
                ],
            }};
            if (args.method.endsWith('get_reversible_compensations')) return {message: {
                request_key: 'b'.repeat(32), rows: [{operation_id: 'a'.repeat(32),
                    counterpart: 'OTHER', amount_usd: 30, compensation_date: '2025-08-01'}],
            }};
            return {message: {pending_usd: 10}};
        },
        msgprint() {}, show_alert() {},
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js'), 'utf8'), context);
(async () => {
    const frm = {doc: {name: 'CREDIT', modified: 'stale-client-version'}, is_dirty: () => false,
        reload_doc: async () => { reloaded++; }};
    await context.cn_reverse_credit_management(frm);
    const choices = dialog.options.fields.find(f => f.fieldname === 'management').options;
    assert.equal(choices.length, 1);
    assert.match(choices[0], /formatted-2025-05-10/);
    assert.match(dialog.options.fields[0].options, /ni libera dinero/i);
    assert.equal(dialog.options.fields.find(f => f.fieldname === 'reason').reqd, 1);
    await dialog.options.primary_action({management: choices[0], event_date: '2026-10-03', reason: 'Corrección'});
    assert.equal(calls.at(-1).args.modified, 'server-version');
    assert.equal(calls.at(-1).args.entry_id, 'ACTIVE');
    assert.equal(calls.at(-1).args.reason, 'Corrección');
    assert.equal(dialog.hidden, true);
    assert.equal(reloaded, 1);
    await context.cn_reverse_compensation(frm);
    const operations = dialog.options.fields.find(f => f.fieldname === 'operation').options;
    assert.equal(operations.length, 1);
    assert.match(operations[0], /OTHER/);
    assert.match(operations[0], /formatted-2025-08-01/);
    assert.equal(dialog.options.fields.find(f => f.fieldname === 'reason').reqd, 1);
    await dialog.options.primary_action({operation: operations[0], reversal_date: '2026-10-03', reason: 'Error de vinculación'});
    assert.equal(calls.at(-1).args.operation_id, 'a'.repeat(32));
    assert.equal(calls.at(-1).args.request_key, 'b'.repeat(32));
    assert.equal(calls.at(-1).args.reason, 'Error de vinculación');
    assert.equal(reloaded, 2);
    assert.equal(frm._cn_reversing_compensation, false);
    console.log('OK: reversal selects only eligible management and sends exact identity/version/reason.');
})().catch(error => { console.error(error); process.exitCode = 1; });
