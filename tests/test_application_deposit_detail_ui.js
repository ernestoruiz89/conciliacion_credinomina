const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const calls = [], messages = [], events = {}, order = [];
let dialog, preview;
const context = vm.createContext({
    __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    frappe: {
        ui: {form: {on: (name, handlers) => {events[name] = handlers;}}, Dialog: class {
            constructor(options) { this.options = options; dialog = this; }
            show() { this.shown = true; }
            hide() { this.shown = false; }
            get_primary_btn() { return {prop: () => {}}; }
        }},
        utils: {escape_html: value => value.replace(/</g, "&lt;").replace(/>/g, "&gt;")},
        msgprint: value => messages.push(value),
        call: async options => {
            order.push("call"); calls.push(options);
            return {message: preview};
        },
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const frm = {doc: {name: "D", detail_period: "P"}, is_new: () => false,
    is_dirty: () => true, save: async () => order.push("save"), reload_doc: async () => order.push("reload")};
(async () => {
    preview = {period: "P", applied_usd: 100, total_usd: 60, deposit_usd: 50,
        rows: [{client_name: "<Ana>", deducted_usd: 60}], fingerprint: "F", replaces_detail: true};
    await events["CN Remittance Allocation"].use_applications_detail(frm);
    assert.deepEqual(order, ["save", "call"]);
    assert.equal(calls.length, 1); // Preview only, no mutation before confirmation.
    assert.ok(dialog.shown);
    assert.ok(dialog.options.fields[0].options.includes("&lt;Ana&gt;"));
    assert.ok(dialog.options.fields[0].options.includes("no coincide"));
    assert.equal(dialog.options.fields[1].fieldname, "replace_detail");
    assert.equal(dialog.options.fields[1].reqd, 1);
    await dialog.options.primary_action({replace_detail: 1});
    assert.ok(calls[1].method.endsWith(".use_application_detail"));
    assert.equal(calls[1].args.fingerprint, "F");
    assert.equal(calls[1].args.replace_detail, 1);
    assert.equal(order.at(-1), "reload");
    assert.equal(dialog.shown, false);
    assert.ok(!calls.some(call => /reconcile|submit/.test(call.method)));
    preview = {rows: []};
    dialog = undefined;
    await events["CN Remittance Allocation"].use_applications_detail(frm);
    assert.equal(dialog, undefined);
    assert.ok(messages.at(-1).includes("No hay aplicaciones pendientes"));
    frm.doc.detail_period = "";
    const before = calls.length;
    await events["CN Remittance Allocation"].use_applications_detail(frm);
    assert.equal(calls.length, before);
    console.log("OK: preview, pending totals, explicit replacement, save-first and no automatic reconciliation.");
})().catch(error => {console.error(error); process.exitCode = 1;});
