const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const events = [];
let dialogOptions;
const result = {employer: "A", imports: 1, rows: 1, matched: 0, pending: 1, ignored: 0,
    pending_rows: [{import_name: "IA", row: 2, client_name: "<script>bad</script>", loan_number: "101", reason: "Falta depósito"}]};
const context = vm.createContext({
    __: (text, values = []) => text.replace(/\{(\d+)\}/g, (_, i) => values[i]),
    frappe: {
        ui: {form: {on() {}}, Dialog: function(options) {
            dialogOptions = options;
            this.show = () => events.push("dialog");
            this.hide = () => {};
        }},
        utils: {escape_html: value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        call: async args => {events.push("call"); assert.equal(args.args.import_name, "IA"); return {message: result};},
        msgprint: () => events.push("message"),
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_source_import/cn_source_import.js"), "utf8"), context);

(async () => {
    const frm = {doc: {employer: "A", name: "IA"}, is_dirty: () => true,
        save: async () => events.push("save"), reload_doc: async () => events.push("reload")};
    await context.reconcileCompany(frm);
    assert.deepEqual(events, ["save", "call", "reload", "dialog"]);
    const html = dialogOptions.fields[0].options;
    assert.ok(html.includes("Falta depósito"));
    assert.ok(html.includes("&lt;script&gt;bad&lt;/script&gt;"));
    assert.ok(!html.includes("<script>"));
    assert.equal(frm.__company_reconciliation_running, false);

    events.length = 0;
    frm.save = async () => {throw new Error("save failed");};
    await assert.rejects(context.reconcileCompany(frm), /save failed/);
    assert.deepEqual(events, []);
    assert.equal(frm.__company_reconciliation_running, false);

    events.length = 0;
    frm.save = async () => events.push("save");
    context.frappe.call = async () => {throw new Error("reconciliation failed");};
    await assert.rejects(context.reconcileCompany(frm), /reconciliation failed/);
    assert.deepEqual(events, ["save"]);
    assert.equal(frm.__company_reconciliation_running, false);

    events.length = 0;
    frm.doc.employer = "";
    await context.reconcileCompany(frm);
    assert.deepEqual(events, ["message"]);
    console.log("Company reconciliation UI checks passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
