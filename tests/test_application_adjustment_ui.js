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
    const updates = [];
    frm.remove_custom_button = label => { delete actions[label]; };
    frm.set_value = async (field, value) => {
        const values = typeof field === "object" ? field : {[field]: value};
        updates.push(values);
        Object.assign(frm.doc, values);
    };
    for (const category of ["Ajuste de aplicación", "Compensación entre partidas"]) {
        frm.doc = {docstatus: 0, category, review_action: "Partida de depósito",
            related_import: "IMPORT", related_application: "APP", application_adjustment_usd: 63.74,
            adjustment_periods: '["P"]', adjustment_collection_rows: '["C"]',
            amount: 63.74, source_debit: 2334.43, source_voucher: "001016152"};
        actions["Confirmar ajuste"] = () => {};
        actions["Compensar con otra partida"] = () => {};
        await handlers.review_action(frm);
        assert.equal(actions["Confirmar ajuste"], undefined);
        assert.equal(actions["Compensar con otra partida"], undefined);
        assert.equal(frm.doc.related_import, "");
        assert.equal(frm.doc.related_application, "");
        assert.equal(frm.doc.application_adjustment_usd, 0);
        assert.equal(frm.doc.category, "Ajuste de conciliación");
        assert.equal(frm.doc.amount, 63.74);
        assert.equal(frm.doc.source_debit, 2334.43);
        assert.equal(frm.doc.source_voucher, "001016152");
    }
    const before = updates.length;
    for (const protection of [{docstatus: 1}, {compensations: [{}]}, {accounting_classification: "ND de Aplicación de pago"}]) {
        frm.doc = {docstatus: 0, review_action: "Partida de depósito", related_application: "APP", ...protection};
        await handlers.review_action(frm);
        assert.equal(frm.doc.related_application, "APP");
    }
    assert.equal(updates.length, before);
    console.log("OK: Vincular a aplicación, guardar antes de confirmar, permiso y recarga.");
})().catch(error => {console.error(error); process.exitCode = 1;});
