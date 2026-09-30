const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8");
let events, dialog;
const calls = [], order = [];
const context = vm.createContext({
    __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]),
    frappe: {ui: {form: {on: (_, handlers) => {events = handlers;}}, Dialog: class {
        constructor(options) {this.options = options; dialog = this;}
        show() {} hide() {this.hidden = true;}
        get_primary_btn() {return {prop() {}};}
    }}, session: {user: "Administrator"}, user: {has_role: () => true},
    call: async args => {calls.push(args); order.push("call"); return {message: {rows: 2}};},
    show_alert: message => {assert.ok(message.message.includes("2 filas"));}},
});
vm.runInContext(source, context);
const label = "Reconocer cobranza como detalle de la empresa";
const frm = {doc: {name: "P1", status: "Pendiente", reconciliation_mode: "Operativa", collection_rows: [{}, {}]},
    fields: [], buttons: new Map(), is_new: () => false, is_dirty: () => true,
    set_df_property() {}, add_custom_button(name, callback, group) {this.buttons.set(name, {callback, group});},
    save: async () => {order.push("save");}, reload_doc: async () => {order.push("reload");},
};
(async () => {
    events.refresh(frm);
    assert.equal(frm.buttons.get(label).group, "Más opciones");
    assert.ok(!frm.buttons.has("Reconocer cobranza por depósito"));
    frm.buttons.get(label).callback();
    assert.ok(dialog.options.fields.find(f => f.fieldname === "confirmed").reqd);
    assert.ok(dialog.options.fields.find(f => f.fieldname === "evidence_date").reqd);
    await dialog.options.primary_action({confirmed: 0});
    assert.equal(calls.length, 0);
    const action = dialog.options.primary_action({confirmed: 1, evidence_date: "2026-09-30"});
    await dialog.options.primary_action({confirmed: 1, evidence_date: "2026-09-30"});
    await action;
    assert.deepEqual(order, ["save", "call", "reload"]);
    assert.ok(calls[0].method.endsWith(".recognize_collection_as_employer_detail"));
    assert.equal(calls[0].args.evidence_date, "2026-09-30");
    assert.ok(dialog.hidden);
    for (const changes of [{reconciliation_mode: "Historica"}, {status: "Cerrado"},
        {deduction_basis: "Detalle de empresa"}, {employer_response_file: "/detail.xlsx"}, {collection_rows: []}]) {
        const form = {...frm, doc: {...frm.doc, ...changes}, buttons: new Map(), disable_save() {}, set_intro() {},
            get_perm: () => true};
        context.frappe.utils = {escape_html: value => value};
        events.refresh(form);
        assert.ok(!form.buttons.has(label));
    }
    console.log("OK: collection recognition menu, confirmation, save-first, date, feedback and duplicate-click guard.");
})().catch(error => {console.error(error); process.exitCode = 1;});
