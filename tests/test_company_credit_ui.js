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

    await context.createRemittanceComplementary({doc: {name: "DEP", modified: "stamp", employer: "EMP"},
        is_new: () => false, is_dirty: () => false, reload_doc: async () => {reloaded++;}});
    assert.ok(dialog.options.fields.find(f => f.fieldname === "category").options.split("\n").includes("Cuenta por Cobrar a la Empresa"));
    const beforeReceivable = creations;
    for (const amount of [0, 10, NaN, Infinity]) {
        await dialog.options.primary_action({category: "Cuenta por Cobrar a la Empresa", amount});
    }
    assert.equal(creations, beforeReceivable);
    await dialog.options.primary_action({category: "Cuenta por Cobrar a la Empresa", amount: -10, currency: "USD"});
    assert.equal(request.args.values.category, "Cuenta por Cobrar a la Empresa");
    assert.equal(request.args.values.amount, -10);
    assert.equal(creations, beforeReceivable + 1);
    assert.match(alerts.at(-1).message, /saldo por cobrar a la empresa/);

    const buttons = {};
    const deposit = {doc: {name: "DEP", docstatus: 1, modified: "stamp", employer: "EMP",
        deposit_date: "2025-07-16", amount_usd: 53.36, allocated_usd: 52.07, unclassified_usd: 1.29},
        is_new: () => false, is_dirty: () => false, get_perm: () => true,
        reload_doc: async () => {reloaded++;}, add_custom_button: (label, action) => {buttons[label] = action;}};
    context.addCompanyCreditButton(deposit);
    assert.equal(typeof buttons["Crear saldo a favor de la empresa"], "function");
    const before = creations;
    await buttons["Crear saldo a favor de la empresa"]();
    assert.equal(creations, before); // Opening the modal never records money.
    assert.equal(dialog.options.title, "Crear saldo a favor de la empresa");
    assert.equal(dialog.values.amount, 1.29);
    assert.equal(dialog.values.currency, "USD");
    assert.equal(dialog.values.category, "Saldo a favor de la empresa");
    assert.ok(dialog.options.fields.find(f => f.fieldname === "category").read_only);
    assert.ok(dialog.options.fields.find(f => f.fieldname === "currency").read_only);
    assert.equal(dialog.options.primary_action_label, "Crear y confirmar saldo a favor");
    const count = dialogCount;
    await buttons["Crear saldo a favor de la empresa"]();
    assert.equal(dialogCount, count);
    for (const amount of [-1, 0, 1.30, NaN]) await dialog.options.primary_action({amount});
    assert.equal(creations, before);
    const values = {amount: 1.29, category: "Otros ingresos", currency: "NIO", credit_client: "OTHER",
        credit_detail_row: "ROW", client_number: "WRONG", loan_number: "WRONG", installment_number: "1",
        reason_type: "Error de la empresa", description: "Exceso confirmado", credit_assigned_to: "Operator",
        credit_commitment_date: "2025-07-20"};
    const submit = dialog.options.primary_action(values);
    await dialog.options.primary_action(values);
    await submit;
    assert.equal(creations, before + 1);
    assert.equal(request.args.values.category, "Saldo a favor de la empresa");
    assert.equal(request.args.values.currency, "USD");
    assert.equal(request.args.values.amount, 1.29);
    assert.equal(request.args.values.credit_assigned_to, "Operator");
    assert.equal(request.args.modified, "stamp");
    for (const field of ["credit_client", "credit_detail_row", "client_number", "loan_number", "installment_number"]) {
        assert.equal(request.args.values[field], "");
    }
    assert.equal(deposit.company_credit_workflow, false);
    for (const pending of [0, -1, NaN]) {
        deposit.doc.unclassified_usd = pending;
        assert.equal(context.canCreateCompanyCredit(deposit), false);
        await context.createCompanyCreditFromDeposit(deposit);
        assert.equal(dialogCount, count);
    }
    deposit.doc.unclassified_usd = 1.29;
    for (const status of [0, 2]) {
        deposit.doc.docstatus = status;
        assert.equal(context.canCreateCompanyCredit(deposit), false);
    }
    deposit.doc.docstatus = 1;
    canCreate = false;
    assert.equal(context.canCreateCompanyCredit(deposit), false);
    canCreate = true; canSubmit = false;
    assert.equal(context.canCreateCompanyCredit(deposit), false);
    canSubmit = true;
    deposit.get_perm = () => false;
    assert.equal(context.canCreateCompanyCredit(deposit), false);
    deposit.get_perm = () => true;
    deposit.is_dirty = () => true;
    deposit.save = async () => {deposit.doc.unclassified_usd = 0.50; deposit.doc.modified = "saved";};
    await context.createCompanyCreditFromDeposit(deposit);
    assert.equal(dialog.values.amount, 0.50); // Suggest the saved balance, not a stale one.
    dialog.hide();
    assert.equal(deposit.company_credit_workflow, false);
    deposit.save = async () => {deposit.doc.unclassified_usd = 0;};
    const beforeEmpty = dialogCount;
    await context.createCompanyCreditFromDeposit(deposit);
    assert.equal(dialogCount, beforeEmpty);
    assert.equal(deposit.company_credit_workflow, false);
    console.log("OK: company credit modal, deposit identification, stale lookup protection and editable invalid identity.");
})().catch(error => {console.error(error); process.exitCode = 1;});
