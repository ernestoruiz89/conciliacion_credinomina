const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let yes, no, confirmations = 0, requests = [], finish, fail = false, reloads = 0;
const messages = [], buttons = {};
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}},
    confirm(message, accept, reject) {confirmations++; yes = accept; no = reject;},
    msgprint(message) {messages.push(message);}, show_alert() {},
    async call(request) {requests.push(request); if (fail) throw Error("Período cerrado"); return new Promise(resolve => {finish = resolve;});},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js", "utf8"), context);
(async () => {
    let permission = true, dirty = false, workflow = false;
    const frm = {doc: {name: "CN-COMP-2026-00533", modified: "STAMP", docstatus: 1, category: "Saldo a favor de la empresa"},
        is_new: () => false, is_dirty: () => dirty, get_perm: (_level, type) => type === "write" || permission,
        toolbar: {has_workflow: () => workflow}, dashboard: {add_comment: message => messages.push(message)},
        add_custom_button: (label, callback) => {buttons[label] = callback;},
        remove_custom_button: label => {delete buttons[label];}, reload_doc: async () => {reloads++;}};
    context.cn_add_cancel_action(frm);
    assert.equal(typeof buttons["Cancelar partida"], "function");
    buttons["Cancelar partida"](); buttons["Cancelar partida"]();
    assert.equal(confirmations, 1);
    const pending = yes();
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0].args)), {item_name: "CN-COMP-2026-00533", modified: "STAMP"});
    assert.ok(requests[0].method.endsWith(".cancel_item"));
    finish({message: {docstatus: 2}}); await pending;
    assert.equal(reloads, 1); assert.equal(frm._cn_cancelling, false);
    buttons["Cancelar partida"](); no(); assert.equal(frm._cn_cancelling, false);
    dirty = true; buttons["Cancelar partida"](); assert.equal(requests.length, 1); dirty = false;
    buttons["Cancelar partida"](); frm.doc.modified = "NEW"; await yes(); assert.equal(requests.length, 1);
    buttons["Cancelar partida"](); fail = true;
    await assert.rejects(yes(), /Período cerrado/); assert.equal(frm._cn_cancelling, false);
    assert.equal(reloads, 1);
    permission = false; context.cn_add_cancel_action(frm); assert.ok(!buttons["Cancelar partida"]);
    assert.ok(messages.some(message => message.includes("permiso de cancelación")));
    permission = true;
    for (const value of [0, 2]) {frm.doc.docstatus = value; context.cn_add_cancel_action(frm); assert.ok(!buttons["Cancelar partida"]);}
    frm.doc.docstatus = 1; workflow = true; context.cn_add_cancel_action(frm); assert.ok(!buttons["Cancelar partida"]);
    workflow = false; frm.doc.origin_deposit = "DEP"; context.cn_add_cancel_action(frm); assert.ok(!buttons["Cancelar partida"]);
    console.log("OK: explicit cancellation, permissions, confirmation, stale requests, retries and no deposit cancellation.");
})().catch(error => {console.error(error); process.exitCode = 1;});
