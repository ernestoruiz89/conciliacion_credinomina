const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {}, calls = [], props = {}, shown = {}, buttons = {};
let dialog, historyHTML = "", reloads = 0, pendingManagement, canCreate = true, canSubmit = true, dialogCount = 0;
const context = vm.createContext({__: (text, values) => values ? text.replace("{0}", values[0]) : text,
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
assert.equal(itemSchema.track_changes, 1);
for (const dt of ["cn_complementary_item", "cn_remittance_allocation"]) {
    vm.runInContext(fs.readFileSync(path.join(base, dt, `${dt}.js`), "utf8"), context);
}
vm.runInContext(fs.readFileSync(path.join(base, "cn_complementary_item/cn_complementary_item_list.js"), "utf8"), context);
const frm = {doctype:"CN Complementary Item", doc: {name:"CREDIT", doctype:"CN Complementary Item", docstatus:0, category:"Saldo a favor del cliente", employer:"CBC",
    registered_deposit:"DEP", credit_client:"123", client_number:"123", modified:"stamp"},
    fields_dict: {credit_history_preview: {$wrapper: {html: value => {historyHTML = value;}}}},
    is_new: () => false, is_dirty: () => false, get_perm: () => true, trigger() {},
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
    for (const field of ["event_date", "amount_usd", "reference", "support_file", "treatment"]) assert.ok(fields.find(f => f.fieldname === field).reqd);
    assert.equal(fields.find(f => f.fieldname === "support_file").options.docname, "CREDIT");
    const task = dialog.options.primary_action({amount_usd:4, treatment:"Devolución", reference:"REC", event_date:"2025-05-10", support_file:"/private/files/proof.pdf"});
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
    const indicator = context.frappe.listview_settings["CN Complementary Item"].get_indicator({category:"Saldo a favor del cliente", docstatus:1, credit_management_status:"Resuelto"});
    assert.equal(indicator[1], "green");
    const added = [];
    context.frappe.listview_settings["CN Complementary Item"].onload({page:{add_inner_button: (label, action) => {if (label === "Saldos de clientes pendientes") action();}}, filter_area:{add: filter => added.push(filter)}});
    assert.ok(JSON.stringify(added).includes("credit_pending_usd"));
    await context.createRemittanceComplementary({doc:{name:"DEP", employer:"INDENICSA", detail_rows:[{name:"R", client:"123", client_name:"Ana", loan_number:"100-1"}]},
        is_new: () => false, is_dirty: () => false, reload_doc: async () => {reloads++;}, paying_companies:["INDENICSA","CBC"]});
    assert.ok(dialog.options.fields.find(f => f.fieldname === "category").options.includes("Saldo a favor del cliente"));
    const reason = dialog.options.fields.find(f => f.fieldname === "reason_type");
    assert.equal(reason.default, "");
    assert.equal(reason.options.split("\n")[0], "");
    for (const category of ["Saldo a favor del cliente", "Saldo a favor de la empresa", "Otros ingresos"]) {
        context.doc = {category};
        for (const condition of [reason.depends_on, reason.mandatory_depends_on]) {
            assert.equal(vm.runInContext(condition.slice(5), context), category !== "Otros ingresos");
        }
    }
    dialog.values.credit_detail_row = "R";
    await dialog.options.fields.find(f => f.fieldname === "credit_detail_row").onchange();
    assert.equal(dialog.values.credit_client, "123");
    await dialog.options.primary_action({category:"Saldo a favor del cliente", amount:10, credit_client:"123", credit_detail_row:"R"});
    assert.ok(calls.find(call => call.message?.includes("No se aplicó a créditos")));
    // Row shortcut reuses the same creation API; only explicit confirmation writes.
    const row = {name:"R", idx:1, source_row:2, client:"123", client_number:"123", client_name:"<script>Ana", loan_number:"100-1", pending_usd:10};
    const deposit = {doc:{name:"DEP", employer:"INDENICSA", docstatus:1, modified:"stamp", deposit_date:"2025-05-10", detail_rows:[row]},
        fields_dict:{detail_rows:{grid:{grid_rows_by_docname:{R:{grid_form:{fields_dict:{
            create_client_credit:{$wrapper:{toggle: value => {shown.rowButton = value;}}},
            select_pending_targets:{$wrapper:{toggle: value => {shown.rowTargetButton = value;}}},
        }}}}}}},
        is_new: () => false, is_dirty: () => false, get_perm: () => true, reload_doc: async () => {reloads++;}, paying_companies:["INDENICSA","CBC"]};
    const detailEvents = handlers["CN Remittance Detail"];
    detailEvents.form_render(deposit, "CN Remittance Detail", "R");
    assert.equal(shown.rowButton, true);
    assert.equal(shown.rowTargetButton, true);
    const createsBefore = calls.filter(call => call.method?.endsWith("create_complementary_item")).length;
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    const rowDialog = dialog, count = dialogCount;
    assert.equal(deposit.detail_credit_workflow, true);
    assert.equal(rowDialog.options.title, "Crear saldo a favor del cliente");
    assert.equal(rowDialog.values.amount, 10);
    assert.equal(rowDialog.values.currency, "USD");
    assert.equal(rowDialog.values.credit_client, "123");
    assert.equal(rowDialog.values.credit_detail_row, "R");
    assert.equal(rowDialog.values.loan_number, "100-1");
    assert.ok(rowDialog.options.fields.find(f => f.fieldname === "category").read_only);
    assert.ok(rowDialog.options.fields.find(f => f.fieldname === "credit_detail_row").read_only);
    assert.ok(rowDialog.options.fields[0].options.includes("&lt;script>"));
    assert.equal(calls.filter(call => call.method?.endsWith("create_complementary_item")).length, createsBefore);
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    assert.equal(dialogCount, count); // Duplicate clicks cannot open a second creation modal.
    for (const amount of [-1, 0, 11, NaN]) await rowDialog.options.primary_action({amount});
    assert.equal(calls.filter(call => call.method?.endsWith("create_complementary_item")).length, createsBefore);
    const values = {amount:10, category:"Cobranza administrativa", currency:"NIO", credit_client:"OTHER", credit_detail_row:"OTHER", client_number:"WRONG", loan_number:"OTHER", reason_type:"Pago adicional no informado"};
    const submit = rowDialog.options.primary_action(values);
    await rowDialog.options.primary_action(values);
    await submit;
    const creation = calls.filter(call => call.method?.endsWith("create_complementary_item")).at(-1);
    assert.equal(creation.args.values.category, "Saldo a favor del cliente");
    assert.equal(creation.args.values.currency, "USD");
    assert.equal(creation.args.values.credit_detail_row, "R");
    assert.equal(creation.args.values.credit_client, "123");
    assert.equal(creation.args.values.client_number, "123");
    assert.equal(creation.args.values.loan_number, "100-1");
    assert.equal(creation.args.values.reason_type, "Pago adicional no informado");
    assert.equal(creation.args.modified, "stamp");
    assert.equal(calls.filter(call => call.method?.endsWith("create_complementary_item")).length, createsBefore + 1);
    assert.equal(deposit.detail_credit_workflow, false);

    for (const pending of [0, -1, NaN]) {
        row.pending_usd = pending;
        detailEvents.form_render(deposit, "CN Remittance Detail", "R");
        assert.equal(shown.rowButton, false);
        assert.equal(shown.rowTargetButton, false);
        await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
        assert.equal(dialogCount, count);
    }
    row.pending_usd = 10;
    for (const docstatus of [0, 2]) {
        deposit.doc.docstatus = docstatus;
        await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
        assert.equal(dialogCount, count);
    }
    deposit.doc.docstatus = 1;
    canCreate = false;
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    canCreate = true; canSubmit = false;
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    assert.equal(dialogCount, count);
    canSubmit = true;
    // Defaults must use the saved row, not the stale object from before save.
    deposit.is_dirty = () => true;
    let saves = 0;
    deposit.save = async () => {saves++; deposit.doc.detail_rows = [{...row, pending_usd:7}]; deposit.doc.modified = "saved";};
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    assert.equal(saves, 1);
    assert.equal(dialog.values.amount, 7);
    dialog.hide();
    assert.equal(deposit.detail_credit_workflow, false);
    deposit.save = async () => {deposit.doc.detail_rows = [];};
    const beforeDeletedRow = dialogCount;
    await detailEvents.create_client_credit(deposit, "CN Remittance Detail", "R");
    assert.equal(dialogCount, beforeDeletedRow);
    assert.equal(deposit.detail_credit_workflow, false);
    console.log("OK: customer credit category, exact optional row, beneficiary company, financial locks, management proof, history escaping, double-click guard and pending list.");
})().catch(error => {console.error(error); process.exitCode = 1;});
