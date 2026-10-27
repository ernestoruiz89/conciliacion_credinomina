const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const handlers = {}, lookups = [], alerts = [];
let dialog, request, reloaded = 0, dialogCount = 0, canCreate = true, canSubmit = true, creations = 0;
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on: (dt, events) => {handlers[dt] = events;}}, Dialog: class {
        constructor(options) {this.options = options; this.values = {}; dialog = this; dialogCount++;}
        async set_values(values) {Object.assign(this.values, values);}
        show() {}
        hide() {this.hidden = true; this.options.onhide?.();}
        get_primary_btn() {return {prop() {}};}
    }},
    db: {get_value: () => new Promise(resolve => lookups.push(resolve))},
    utils: {escape_html: value => value},
    datetime: {get_today: () => "2026-10-09"},
    model: {can_create: () => canCreate, can_submit: () => canSubmit},
    call: async args => {request = args; creations++; return {message: {name: "CREDIT",
        company_credit: args.args.values.category === "Saldo a favor de la empresa",
        company_receivable: args.args.values.category === "Cuenta por Cobrar a la Empresa", result: "Pendiente"}};},
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

    const documentary = [];
    context.credinomina = {document_difference: async options => documentary.push(options)};
    const deposit = {doc: {doctype: "CN Remittance Allocation", name: "DEP", docstatus: 1},
        is_new: () => false, is_dirty: () => false, reload_doc: async () => {}};
    await context.createRemittanceComplementary(deposit, {detailRowName: "ROW"});
    assert.equal(documentary[0].name, "DEP");
    assert.equal(documentary[0].parentfield, "detail_rows");
    assert.equal(documentary[0].row_name, "ROW");
    assert.equal(documentary[0].amount, undefined);
    assert.equal(context.canCreateCompanyCredit(deposit), false);
    console.log("OK: legacy credit history preserved; new differences open documentary classification.");
})().catch(error => {console.error(error); process.exitCode = 1;});
