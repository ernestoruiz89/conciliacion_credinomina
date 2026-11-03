const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let dialog, requests = [], reloads = 0, dirty = false, permission = true, workflow = false, fail = false, finish;
const buttons = {}, handlers = {}, messages = [];
const context = vm.createContext({__: s => s, frappe: {
    ui: {form: {on: (doctype, events) => {handlers[doctype] = events;}}, Dialog: function(options) {
        Object.assign(this, options); dialog = this;
        this.show = this.hide = this.disable_primary_action = this.enable_primary_action = () => {};
    }}, msgprint: m => messages.push(m), show_alert() {},
    call(request) {requests.push(request); if (fail) return Promise.reject(Error("Sin rol"));
        return new Promise(resolve => {finish = resolve;});},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/public/js/document_reopening.js", "utf8"), context);
(async () => {
    const frm = {doc: {doctype: "CN Complementary Item", name: "X", modified: "STAMP", docstatus: 2},
        meta: {is_submittable: true}, is_new: () => false, is_dirty: () => dirty, get_perm: () => permission,
        toolbar: {has_workflow: () => workflow}, add_custom_button: (label, fn) => {buttons[label] = fn;},
        remove_custom_button: label => {delete buttons[label];}, reload_doc: async () => {reloads++;}};
    assert.deepEqual(Object.keys(handlers).sort(), ["CN Accounting Import", "CN Complementary Item", "CN Remittance Allocation"]);
    handlers[frm.doc.doctype].refresh(frm);
    buttons["Deshacer cancelación"]();
    assert.ok(dialog.fields[0].options.includes("borrador"));
    let pending = dialog.primary_action({reason: "Accidental"});
    await dialog.primary_action({reason: "Accidental"});
    assert.equal(requests.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0].args)), {doctype: frm.doc.doctype, name: "X", modified: "STAMP", reason: "Accidental"});
    finish({message: {docstatus: 0}}); await pending;
    assert.equal(reloads, 1); assert.equal(frm._cn_reopening, false);
    buttons["Deshacer cancelación"](); frm.doc.modified = "NEW";
    await dialog.primary_action({reason: "Accidental"}); assert.equal(requests.length, 1);
    buttons["Deshacer cancelación"](); fail = true;
    await assert.rejects(dialog.primary_action({reason: "Accidental"}), /Sin rol/);
    assert.equal(frm._cn_reopening, false); assert.equal(reloads, 1);
    for (const status of [0, 1]) {frm.doc.docstatus = status; handlers[frm.doc.doctype].refresh(frm); assert.ok(!buttons["Deshacer cancelación"]);}
    frm.doc.docstatus = 2; permission = false; handlers[frm.doc.doctype].refresh(frm); assert.ok(!buttons["Deshacer cancelación"]);
    permission = true; workflow = true; handlers[frm.doc.doctype].refresh(frm); assert.ok(!buttons["Deshacer cancelación"]);
    vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js", "utf8"), context);
    workflow = false; fail = false; frm.doc.docstatus = 1;
    context.cn_unconfirm_item(frm);
    const count = requests.length;
    pending = dialog.primary_action({reason: "Revisar vínculos"});
    assert.equal(requests.length, count + 1);
    assert.ok(requests.at(-1).method.endsWith(".unconfirm_item"));
    assert.equal(requests.at(-1).args.item_name, "X");
    assert.ok(dialog.fields[0].options.includes("borrador"));
    finish({message: {docstatus: 0}}); await pending;
    context.cn_unconfirm_item(frm); frm.doc.modified = "CHANGED";
    await dialog.primary_action({reason: "Revisar vínculos"});
    assert.equal(requests.length, count + 1);
    console.log("OK: restoration returns to draft, permissions, workflow, stale request, double-click and retry guards.");
})().catch(error => {console.error(error); process.exitCode = 1;});
