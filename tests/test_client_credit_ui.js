const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {}, calls = [], props = {}, shown = {}, buttons = {};
let dialog, historyHTML = "", reloads = 0, pendingManagement, canCreate = true, canSubmit = true, dialogCount = 0;
const context = vm.createContext({window: {}, setTimeout: callback => callback(), __: (text, values) => values ? text.replace("{0}", values[0]) : text,
    format_currency: amount => `USD ${amount}`, frappe: {
    ui: {form: {on: (dt, events) => {handlers[dt] = events;}}, Dialog: class {
        constructor(options) {this.options = options; this.values = {}; dialog = this; dialogCount++;}
        get_value(field) {return this.values[field];}
        async set_values(values) {Object.assign(this.values, values);}
        get_primary_btn() {return {prop: (key, value) => {this[key] = value;}};}
        show() {} hide() {this.hidden = true; this.options.onhide?.();}
    }}, session: {user: "Operator"}, datetime: {get_today: () => "2026-10-02", str_to_user: date => `display:${date}`},
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;")}, listview_settings: {},
    model: {can_create: () => canCreate, can_submit: () => canSubmit},
    db: {get_value: async (_dt, name) => ({message: {client_name: "Ana", client_number: "123", employer: "CBC", deposit_reference: name, deposit_date: "2025-05-10"}})},
    call: async request => {calls.push(request);
        if (request.method.endsWith("record_management")) return new Promise(resolve => {pendingManagement = resolve;});
        if (request.method.endsWith("get_deposit_detail")) return {message: {rows: [{name:"R", client:"123", client_number:"123", client_name:"Ana", loan_number:"100-1", amount_usd:110}]}};
        return {message: {name: "CREDIT", client_credit: true}};
    }, msgprint: message => calls.push(message), show_alert: message => calls.push(message),
}});
const base = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype");
const itemSchema = JSON.parse(fs.readFileSync(path.join(base, "cn_complementary_item/cn_complementary_item.json"), "utf8"));
const reasonField = itemSchema.fields.find(field => field.fieldname === "reason_type");
assert.equal(reasonField.allow_on_submit, 1);
assert.ok(!reasonField.read_only);
assert.ok(reasonField.options.split("\n").includes("Por refinanciamiento"));
assert.ok(reasonField.options.split("\n").includes("Por cancelación"));
assert.equal(itemSchema.track_changes, 1);
for (const dt of ["cn_complementary_item", "cn_remittance_allocation"]) {
    vm.runInContext(fs.readFileSync(path.join(base, dt, `${dt}.js`), "utf8"), context);
}
vm.runInContext(fs.readFileSync(path.join(base, "cn_complementary_item/cn_complementary_item_list.js"), "utf8"), context);
const frm = {doctype:"CN Complementary Item", doc: {name:"CREDIT", doctype:"CN Complementary Item", docstatus:0, category:"Saldo a favor del cliente", employer:"CBC",
    registered_deposit:"DEP", credit_client:"123", client_number:"123", modified:"stamp"},
    fields_dict: {credit_history_preview: {$wrapper: {html: value => {historyHTML = value;}}}},
    is_new: () => false, is_dirty: () => false, get_perm: () => true, trigger() {}, page: {add_menu_item() {}},
    set_value: async (key, value) => typeof key === "string" ? frm.doc[key] = value : Object.assign(frm.doc, key),
    set_df_property: (field, prop, value) => {props[field + prop] = value;}, toggle_display: (field, value) => {shown[field] = value;},
    add_custom_button: (name, action) => {buttons[name] = action;}, reload_doc: async () => {reloads++;},
    dashboard: {set_headline_alert: text => calls.push(text), clear_headline() {}},
};
(async () => {
    const events = handlers["CN Complementary Item"];
    events.category(frm);
    assert.equal(frm.doc.review_action, "Saldo a favor del cliente");
    assert.equal(frm.doc.credit_assigned_to, "Operator");
    await events.registered_deposit(frm);
    assert.equal(frm.doc.employer, "CBC"); // INDENICSA payer must not overwrite CBC beneficiary.
    await events.select_credit_detail(frm);
    assert.equal(dialog.options.fields[0].options[1].value, "R");
    await dialog.options.primary_action({row:"R"});
    assert.equal(frm.doc.credit_detail_row, "R");
    assert.equal(frm.doc.loan_number, "100-1");
    await events.select_credit_detail(frm);
    await dialog.options.primary_action({row:""});
    assert.equal(frm.doc.credit_detail_row, ""); // Optional outside-detail excess.
    frm.doc.docstatus = 1;
    frm.doc.credit_pending_usd = 10;
    frm.doc.credit_history = '[{"fecha":"2025-05-10","tratamiento":"Devolución","importe_usd":4,"referencia":"<script>bad","usuario":"Operator"}]';
    events.refresh(frm);
    assert.equal(props.categoryread_only, 1);
    assert.equal(props.registered_depositread_only, 1);
    assert.ok(!props.reason_typeread_only);
    assert.equal(shown.record_credit_management, true);
    assert.match(historyHTML, /display:2025-05-10/);
    assert.match(historyHTML, /&lt;script>/);
    assert.equal(buttons["Vincular a aplicación"], undefined);
    await events.record_credit_management(frm);
    const fields = dialog.options.fields;
    for (const field of ["event_date", "amount_usd", "reference", "treatment"]) assert.ok(fields.find(f => f.fieldname === field).reqd);
    assert.ok(!fields.find(f => f.fieldname === "support_file").reqd);
    assert.equal(fields.find(f => f.fieldname === "support_file").options.docname, "CREDIT");
    const task = dialog.options.primary_action({amount_usd:4, treatment:"Devolución", reference:"REC", event_date:"2025-05-10"});
    await dialog.options.primary_action({amount_usd:4});
    assert.equal(calls.filter(call => call.method?.endsWith("record_management")).length, 1);
    const request = calls.find(call => call.method?.endsWith("record_management"));
    assert.equal(request.args.modified, "stamp");
    pendingManagement({message:{pending_usd:6}}); await task;
    assert.equal(dialog.hidden, true);
    assert.equal(reloads, 1);
    frm.doc.credit_pending_usd = 0;
    events.refresh(frm);
    assert.equal(shown.record_credit_management, false);
    const indicator = context.frappe.listview_settings["CN Complementary Item"].get_indicator({category:"Saldo a favor del cliente", docstatus:1, credit_management_status:"Resuelto", reconciliation_status:"Conciliado", _cn_balance:{state:"Conciliado"}});
    assert.equal(indicator[1], "green");
    const added = [];
    context.frappe.listview_settings["CN Complementary Item"].onload({page:{add_inner_button: (label, action) => {if (label === "Saldos de clientes pendientes") action();}}, filter_area:{add: filter => added.push(filter)}});
    assert.ok(JSON.stringify(added).includes("credit_pending_usd"));
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
