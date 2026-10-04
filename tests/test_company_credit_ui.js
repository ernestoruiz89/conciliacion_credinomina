const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const handlers = {}, lookups = [], alerts = [];
let dialog, request, reloaded = 0;
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on: (dt, events) => {handlers[dt] = events;}}, Dialog: class {
        constructor(options) {this.options = options; dialog = this;}
        show() {}
        hide() {this.hidden = true;}
        get_primary_btn() {return {prop() {}};}
    }},
    db: {get_value: () => new Promise(resolve => lookups.push(resolve))},
    utils: {escape_html: value => value},
    call: async args => {request = args; return {message: {name: "CREDIT", company_credit: true, result: "Saldo a favor documentado"}};},
    msgprint: message => alerts.push(message),
}});
const base = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype");
for (const dt of ["cn_complementary_item", "cn_remittance_allocation"]) {
    vm.runInContext(fs.readFileSync(path.join(base, dt, `${dt}.js`), "utf8"), context);
}
(async () => {
    const events = handlers["CN Complementary Item"];
    const shown = {}, properties = {};
    const frm = {doc: {category: "Saldo a favor de la empresa", registered_deposit: "DEP"},
        toggle_display: (field, value) => {shown[field] = value;},
        set_df_property: (field, prop, value) => {properties[field + prop] = value;},
        set_value: async values => Object.assign(frm.doc, values)};
    events.category(frm);
    assert.equal(shown.client_number, false);
    assert.equal(properties.referenceread_only, true);
    frm.doc.client_number = "123";
    events.category(frm);
    assert.equal(shown.client_number, true); // The user must be able to clear an invalid prefill.
    let task = events.registered_deposit(frm);
    lookups.shift()({message: {employer: "EMP", deposit_reference: "REF", deposit_voucher: "BANK", deposit_date: "2025-05-10"}});
    await task;
    assert.equal(frm.doc.reference, "REF");
    assert.equal(frm.doc.posting_date, "2025-05-10");
    task = events.registered_deposit(frm);
    frm.doc.registered_deposit = "OTHER";
    lookups.shift()({message: {deposit_reference: "STALE"}});
    await task;
    assert.equal(frm.doc.reference, "REF");

    await context.createRemittanceComplementary({doc: {name: "DEP", modified: "stamp", employer: "EMP"},
        is_new: () => false, is_dirty: () => false, reload_doc: async () => {reloaded++;}});
    assert.ok(dialog.options.fields.find(f => f.fieldname === "category").options.includes("Saldo a favor de la empresa"));
    assert.ok(dialog.options.fields.find(f => f.fieldname === "reason_type").mandatory_depends_on);
    assert.ok(dialog.options.fields.find(f => f.fieldname === "credit_assigned_to").mandatory_depends_on.includes("Saldo a favor de la empresa"));
    assert.ok(dialog.options.fields.find(f => f.fieldname === "credit_commitment_date").mandatory_depends_on.includes("Saldo a favor de la empresa"));
    await dialog.options.primary_action({category: "Saldo a favor de la empresa", amount: 10});
    assert.equal(request.args.remittance_name, "DEP");
    assert.equal(reloaded, 1);
    assert.equal(dialog.hidden, true);
    assert.match(alerts[0].message, /saldo a favor/i);
    console.log("OK: company credit modal, deposit identification, stale lookup protection and editable invalid identity.");
})().catch(error => {console.error(error); process.exitCode = 1;});
