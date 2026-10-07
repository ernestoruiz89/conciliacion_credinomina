const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
let handlers;
const events = [];
const context = vm.createContext({
    __: text => text,
    frappe: {
        ui: {form: {on(_name, actions) {handlers = actions;}}},
        call: async args => events.push(args),
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
    for (const state of [{status: "Cerrado"}, {status: "Pendiente", reconciliation_mode: "Historica"},
        {status: "Pendiente", reconciliation_mode: "Operativa", collection_rows: []}]) {
        Object.assign(frm.doc, state); buttons.clear(); handlers.refresh(frm);
        assert.ok(!buttons.has("Conciliación 1: validar aplicaciones"));
    }
    console.log("PASS: first-stage action saves, calls its own endpoint, reloads; closed/historical/empty periods excluded.");
})().catch(error => {console.error(error); process.exitCode = 1;});
