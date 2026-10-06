const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const calls = [], order = [];
let dialog;
const plan = {fingerprint: "fp", total_usd: 100, applied_usd: 90, adjustment_usd: 10,
    rows: [{period: "P", collection_row: "R", client_name: "<script>bad()</script>",
        loan_number: "100-1", basis: "Cobranza", base_usd: 100, applied_usd: 90, amount_usd: 10}],
    adjustments: [{collection_row: "R", category: "Otros ingresos"}]};
const context = vm.createContext({
    __: text => text, format_currency: x => `USD ${Number(x).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}, Dialog: class {
        constructor(options) {this.options = options; dialog = this;}
        show() {} hide() {this.hidden = true;}
        disable_primary_action() {this.disabled = true;} enable_primary_action() {this.disabled = false;}
    }}, utils: {escape_html: text => String(text).replaceAll("<", "&lt;")},
    call: async request => {calls.push(request); order.push("call"); return {message:
        request.method.endsWith("preview_transfer") ? plan : {result: "Conciliado"}};},
    msgprint() {}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const frm = {doc: {name: "D"}, is_dirty: () => true, save: async () => order.push("save"),
    reload_doc: async () => order.push("reload")};
(async () => {
    await context.usePreparedReconciliation(frm);
    assert.deepEqual(order, ["save", "call"]);
    assert.equal(calls.length, 1, "Preview must never materialize items");
    const html = dialog.options.fields[0].options;
    assert.ok(html.includes("&lt;script>"));
    assert.ok(!html.includes("<script>"));
    assert.ok(html.includes("Otros ingresos"));
    assert.ok(html.includes("USD 10.00"));
    assert.equal(dialog.options.fields[1].reqd, 1);
    await dialog.options.primary_action({confirm_correspondence: 0});
    assert.equal(calls.length, 1);
    const first = dialog.options.primary_action({confirm_correspondence: 1});
    await dialog.options.primary_action({confirm_correspondence: 1});
    await first;
    assert.equal(calls.length, 2);
    assert.equal(calls[1].args.fingerprint, "fp");
    assert.equal(calls[1].args.remittance_name, "D");
    assert.ok(dialog.hidden && !dialog.disabled);
    assert.equal(order.at(-1), "reload");
    console.log("OK: prepared transfer preview, escaped rows, explicit confirmation, save-first, fingerprint and double-click guard.");
})().catch(error => {console.error(error); process.exitCode = 1;});
