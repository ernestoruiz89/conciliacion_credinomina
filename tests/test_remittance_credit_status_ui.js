const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8");
let dirty = true, calls = [], messages = [], alerts = [], reloaded = 0, resolveCall;
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}}, msgprint: text => messages.push(text), show_alert: value => alerts.push(value),
    call: args => {calls.push(args); return new Promise(resolve => {resolveCall = resolve;});},
}});
vm.runInContext(source, context);
const frm = {doc: {name: "DEP-1", modified: "2026-10-27 10:00:00"},
    is_dirty: () => dirty, reload_doc: async () => {reloaded++;}};
(async () => {
    assert.match(source, /add_custom_button\(__\("Actualizar estado del crédito"\).*__\("Más opciones"\)/);
    await context.refreshRemittanceCreditStatus(frm);
    assert.equal(calls.length, 0);
    assert.equal(messages.length, 1);
    dirty = false;
    const pending = context.refreshRemittanceCreditStatus(frm);
    await context.refreshRemittanceCreditStatus(frm);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "credinomina_reconciliation.remittance_portfolio.refresh_credit_status");
    assert.equal(calls[0].args.remittance_name, "DEP-1");
    assert.equal(calls[0].args.modified, "2026-10-27 10:00:00");
    resolveCall({message: {updated: 2, unidentified: 1}});
    await pending;
    assert.equal(reloaded, 1);
    assert.equal(alerts[0].indicator, "orange");
    assert.equal(frm._cnRefreshingCreditStatus, false);
    console.log("OK: refresh action requires saved form, prevents duplicate calls and reloads results.");
})().catch(error => {console.error(error); process.exitCode = 1;});
