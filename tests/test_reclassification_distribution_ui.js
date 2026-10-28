const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const calls = [], values = {}; let dialog, reloads = 0;
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on() {}}, Dialog: function (options) {
        Object.assign(this, options); this.get_value = key => values[key];
        this.set_value = async (key, value) => {values[key] = value;};
        this.set_df_property = (field, key, value) => {this.fields.find(f => f.fieldname === field)[key] = value;};
        this.get_primary_btn = () => ({prop() {}}); this.show = () => {}; this.hide = () => {};
        dialog = this;
    }}, utils: {escape_html: value => value}, msgprint() {}, show_alert() {},
    call: async args => {
        calls.push(args);
        if (args.method.endsWith("distribution_history")) return {message: {modified: "ITEM-1", remaining_usd: .10, entries: []}};
        if (args.method.endsWith("preview_destination")) return {message: {modified: "DEP-1", rows: [
            {name: "ROW", source_row: 2, employer: "EMPRESA B", client_name: "Cliente", loan_number: "L", amount_usd: 5, pending_usd: 1},
            ...(args.args.override ? [{name: "OTHER", source_row: 3, employer: "EMPRESA C", client_name: "Otro", amount_usd: 5, pending_usd: .5}] : [])]}};
        return {message: {remaining_usd: .09}};
    }
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
const frm = {doc: {name: "CORE", employer: "EMPRESA A", source_debit: .10, source_credit: 0},
    is_dirty: () => false, reload_doc: async () => {reloads++;}};
(async () => {
    await context.cn_distribute_core_item(frm);
    assert.equal(dialog.fields.find(f => f.fieldname === "kind").options.length, 4);
    values.deposit = "DEP"; values.override = 0;
    await dialog.fields.find(f => f.fieldname === "deposit").onchange();
    assert.equal(dialog.fields.find(f => f.fieldname === "target").options.length, 2);
    values.override = 1;
    await dialog.fields.find(f => f.fieldname === "override").onchange();
    assert.equal(dialog.fields.find(f => f.fieldname === "target").options.length, 3);
    assert.equal(calls.at(-1).args.override, 1);
    values.target = dialog.fields.find(f => f.fieldname === "target").options[2];
    values.override = 0;
    await dialog.fields.find(f => f.fieldname === "override").onchange();
    assert.equal(dialog.fields.find(f => f.fieldname === "target").options.length, 2);
    assert.equal(values.target, "");
    assert.equal(calls.at(-1).args.override, 0);
    values.override = 1;
    await dialog.fields.find(f => f.fieldname === "override").onchange();
    const option = dialog.fields.find(f => f.fieldname === "target").options[1];
    await dialog.primary_action({deposit: "DEP", target: option, override: 1, amount_usd: .01,
        kind: "Saldo a favor de la empresa", reason: "Pago duplicado"});
    assert.equal(calls.at(-1).method.endsWith("link_deposit"), true);
    assert.equal(calls.at(-1).args.detail_name, "ROW");
    assert.equal(calls.at(-1).args.kind, "Saldo a favor de la empresa contra depósito");
    assert.equal(calls.at(-1).args.override, 1);
    assert.equal(calls.at(-1).args.reason, "Pago duplicado");
    assert.equal(reloads, 1);
    frm.doc.source_debit = 0; frm.doc.source_credit = .10;
    await context.cn_distribute_core_item(frm);
    assert.deepEqual(Array.from(dialog.fields.find(f => f.fieldname === "kind").options), ["Pago pendiente contra aplicación contable"]);
    values.import_name = "IMPORT"; values.override = 1;
    await dialog.fields.find(f => f.fieldname === "import_name").onchange();
    const application = dialog.fields.find(f => f.fieldname === "target").options[1];
    await dialog.primary_action({import_name: "IMPORT", target: application, override: 1, amount_usd: .01,
        kind: "Pago pendiente contra aplicación contable", reason: "Ajuste verificado"});
    assert.equal(calls.at(-1).method.endsWith("link_application"), true);
    assert.equal(calls.at(-1).args.application_name, "ROW");
    assert.equal(calls.at(-1).args.override, 1);
    assert.equal(reloads, 2);
    console.log("OK: split distribution submits an explicit target and treatment.");
})().catch(error => {console.error(error); process.exitCode = 1;});
