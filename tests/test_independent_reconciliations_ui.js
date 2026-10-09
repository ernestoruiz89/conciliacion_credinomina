const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
let handlers;
const events = [];
const messages = [];
const context = vm.createContext({
    __: text => text,
    frappe: {
        ui: {form: {on(_name, actions) {handlers = actions;}}},
        call: async args => {events.push(args); return {message: {reviewed: 4, conforming: 3, differences: 1, missing_basis: 0}};},
        msgprint: message => messages.push(message),
        session: {user: "operator"}, user: {has_role: () => false},
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
for (const name of ["setPeriodEditing", "setRemittanceDateEditing", "refreshPeriodExceptions",
    "refreshPeriodPending", "refreshApplicationQuality", "addTemplateButtons", "addExportButton"]) context[name] = () => {};
const buttons = new Map();
const frm = {
    doc: {name: "P", status: "Pendiente", reconciliation_mode: "Operativa", collection_rows: [{}]},
    fields_dict: {}, set_df_property() {}, is_new: () => false, is_dirty: () => true,
    get_perm: () => true,
    add_custom_button: (label, action) => buttons.set(label, action),
    save: async () => {events.push("save");}, reload_doc: async () => events.push("reload"),
};
(async () => {
    handlers.refresh(frm);
    await buttons.get("Conciliación 1: validar aplicaciones")();
    assert.equal(events[0], "save");
    assert.ok(events[1].method.endsWith(".reconcile_first"));
    assert.equal(events[1].args.period_name, "P");
    assert.equal(events[2], "reload");
    assert.equal(messages[0].indicator, "orange");
    assert.equal(frm._cn_reconciling_collection, false);
    for (const response of [undefined, {}, {exc: 'error'}, {message: null}]) {
        events.length = messages.length = 0;
        context.frappe.call = async () => response;
        await buttons.get("Conciliación 1: validar aplicaciones")();
        assert.equal(messages[0].indicator, "red");
        assert.ok(!events.includes("reload"));
        assert.equal(frm._cn_reconciling_collection, false);
    }
    context.frappe.call = async () => {throw new Error("network");};
    messages.length = 0;
    await buttons.get("Conciliación 1: validar aplicaciones")();
    assert.match(messages[0].message, /No se pudo completar/);
    assert.equal(frm._cn_reconciling_collection, false);
    let release;
    let calls = 0;
    context.frappe.call = () => {calls++; return new Promise(resolve => {release = resolve;});};
    const running = buttons.get("Conciliación 1: validar aplicaciones")();
    await Promise.resolve();
    await buttons.get("Conciliación 1: validar aplicaciones")();
    assert.equal(calls, 1);
    release({message: {reviewed: 4, conforming: 4, differences: 0, missing_basis: 0}});
    await running;
    assert.equal(messages.at(-1).indicator, "green");
    context.frappe.call = async () => ({message: {reviewed: 4, conforming: 4, differences: 0, missing_basis: 0}});
    const reload = frm.reload_doc;
    frm.reload_doc = async () => {throw new Error("reload");};
    await buttons.get("Conciliación 1: validar aplicaciones")();
    assert.match(messages.at(-1).message, /terminó.*actualizar/);
    frm.reload_doc = reload;
    const save = frm.save;
    frm.save = async () => {throw new Error("save");};
    calls = 0;
    context.frappe.call = async () => {calls++;};
    await buttons.get("Conciliación 1: validar aplicaciones")();
    assert.equal(calls, 0);
    assert.equal(messages.at(-1).indicator, "red");
    assert.equal(frm._cn_reconciling_collection, false);
    frm.save = save;
    for (const state of [{status: "Cerrado"}, {status: "Pendiente", reconciliation_mode: "Historica"},
        {status: "Pendiente", reconciliation_mode: "Operativa", collection_rows: []}]) {
        Object.assign(frm.doc, state); buttons.clear(); handlers.refresh(frm);
        assert.ok(!buttons.has("Conciliación 1: validar aplicaciones"));
    }
    console.log("PASS: first-stage action saves, calls its own endpoint, reloads; closed/historical/empty periods excluded.");
})().catch(error => {console.error(error); process.exitCode = 1;});
