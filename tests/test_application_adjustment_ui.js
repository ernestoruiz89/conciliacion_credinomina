const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let handlers, allow = true;
const actions = {}, calls = [];
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on: (_name, events) => { handlers = events; }}},
    confirm: async (_message, action) => action(),
    call: async args => calls.push(args), show_alert: () => calls.push("alert"),
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
const frm = {doc: {name: "NC", docstatus: 0, category: "Ajuste de aplicación", review_action: "Ajuste de aplicación", accounting_source_key: "evidence"},
    is_new: () => false, is_dirty: () => true, get_perm: () => allow,
    set_df_property() {}, trigger() {}, add_custom_button: (label, action) => { actions[label] = action; },
    dashboard: {set_headline_alert() {}}, save: async () => calls.push("save"), reload_doc: async () => calls.push("reload")};
(async () => {
    handlers.refresh(frm);
    assert.equal(typeof actions["Vincular a aplicación"], "function");
    assert.equal(typeof actions["Confirmar ajuste"], "function");
    await actions["Confirmar ajuste"]();
    assert.equal(calls[0], "save");
    assert.equal(calls[1].args.item_name, "NC");
    assert.ok(calls[1].method.endsWith(".confirm_adjustment"));
    assert.equal(calls[2], "reload");
    delete actions["Confirmar ajuste"];
    allow = false;
    handlers.refresh(frm);
    assert.equal(actions["Confirmar ajuste"], undefined);
    console.log("OK: Vincular a aplicación, guardar antes de confirmar, permiso y recarga.");
})().catch(error => {console.error(error); process.exitCode = 1;});
