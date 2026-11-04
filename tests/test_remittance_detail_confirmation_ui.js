const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js", "utf8");
const start = source.indexOf("async function importRemittanceDetail(frm)");
const end = source.indexOf("async function showRemittanceComplementaryItems", start);
let yes, no, calls = 0, saves = 0, reloads = 0, prompts = 0, fail = false;
const context = vm.createContext({__: value => value, frappe: {
    confirm(message, accept, cancel) {assert.ok(message.includes("¿Desea cargar")); prompts++; yes = accept; no = cancel;},
    async call() {calls++; if (fail) throw new Error("Upload failed");},
}});
vm.runInContext(source.slice(start, end), context);
const frm = {doc: {name: "DEP"}, is_dirty: () => true,
    async save() {saves++;}, async reload_doc() {reloads++;}};
(async () => {
    let pending = context.importRemittanceDetail(frm);
    assert.equal(calls, 0); assert.equal(saves, 0);
    await context.importRemittanceDetail(frm);
    assert.equal(prompts, 1, "Double clicks must not open another prompt");
    no(); await pending;
    assert.equal(calls, 0); assert.equal(saves, 0); assert.equal(reloads, 0);
    pending = context.importRemittanceDetail(frm); yes(); await pending;
    assert.equal(calls, 1); assert.equal(saves, 1); assert.equal(reloads, 1);
    fail = true; pending = context.importRemittanceDetail(frm); yes();
    await assert.rejects(pending, /Upload failed/);
    assert.equal(frm.__loading_deposit_detail, false, "Errors must allow retry");
    console.log("OK: confirm before save/import, cancellation, double clicks and retry.");
})().catch(error => {console.error(error); process.exitCode = 1;});
