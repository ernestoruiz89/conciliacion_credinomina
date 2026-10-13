const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const root = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype");
const schemas = Object.fromEntries(["cn_remittance_allocation", "cn_complementary_item", "cn_deposit_accounting_source"].map(name => [name, JSON.parse(fs.readFileSync(path.join(root, name, name + ".json"), "utf8"))]));
const field = (dt, name) => schemas[dt].fields.find(row => row.fieldname === name);
assert.equal(field("cn_remittance_allocation", "accounting_sources").read_only, 1);
assert.equal(field("cn_remittance_allocation", "accounting_sources").allow_on_submit, 1);
assert.ok(field("cn_remittance_allocation", "accounting_tab").depends_on.includes("accounting_sources"));
assert.ok(field("cn_complementary_item", "credit_assigned_to").mandatory_depends_on.includes("CxC a la empresa"));
assert.equal(field("cn_complementary_item", "receivable_due_date").allow_on_submit, 1);
assert.ok(field("cn_complementary_item", "credit_commitment_date").description.includes("no cambia"));
const handlers = {}, calls = []; let dialog, reloads = 0;
const context = vm.createContext({__: value => value, frappe: {ui: {form: {on: (dt, events) => handlers[dt] = events}, Dialog: class {
    constructor(options) { dialog = this; this.options = options; }
    show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
}}, call: async request => { calls.push(request); return {message: {}}; }, show_alert() {}, msgprint() {},
    utils: {escape_html: String}, datetime: {}, db: {get_value: async () => ({message: {client_name: "Ana", client_number: "1", employer: "EMPRESA ACTUAL"}})}}});
vm.runInContext(fs.readFileSync(path.join(root, "cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const frm = {doc: {name: "D", employer: "EMPRESA ANTERIOR", docstatus: 0, accounting_source_key: "ORIGEN", modified: "M", detail_rows: [], targets: []},
    is_dirty: () => false, reload_doc: async () => reloads++};
context.frm = frm;
vm.runInContext("reviewDepositOrigin(frm)", context);
assert.equal(dialog.options.fields.find(row => row.fieldname === "action").default, "Vincular origen a otro depósito");
assert.equal(dialog.options.fields.find(row => row.fieldname === "reason").reqd, 1);
(async () => {
    await dialog.options.primary_action({action: "Vincular origen a otro depósito", canonical_name: "D2", reason: "Mismo comprobante"});
    assert.ok(calls.at(-1).method.endsWith("link_duplicate_origin"));
    assert.equal(calls.at(-1).args.canonical_name, "D2");
    assert.equal(reloads, 1);
    vm.runInContext(fs.readFileSync(path.join(root, "cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
    const credit = {doc: {docstatus: 0, registered_deposit: "D", credit_client: "1", employer: "EMPRESA ANTERIOR"},
        async set_value(values) {Object.assign(this.doc, values);}};
    await handlers["CN Complementary Item"].credit_client(credit);
    assert.equal(credit.doc.employer, "EMPRESA ANTERIOR");
    assert.equal(credit.doc.client_name, "Ana");
    console.log("Financial origin UI controls: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
