const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let dialog, reloads = 0;
const calls = [], messages = [], values = {};
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}, Dialog: function (options) {
        Object.assign(this, options);
        this.fields_dict = {dates: {$wrapper: {text() {}}}};
        this.set_df_property = (field, key, value) => {this.fields.find(f => f.fieldname === field)[key] = value;};
        this.get_value = key => values[key];
        this.set_value = async (key, value) => {values[key] = value;};
        this.show = () => {}; this.hide = () => {};
        this.disable_primary_action = () => {}; this.enable_primary_action = () => {};
        dialog = this;
    }}, datetime: {now_date: () => "2026-10-27"}, msgprint: text => messages.push(text), show_alert() {},
    call: async args => {
        calls.push(args);
        if (args.method.endsWith("preview")) return {message: {item_modified: "I1", deposit_modified: "D1",
            credit_origin_date: "2025-08-07", reconciliation_date: "2025-11-21",
            rows: [{name: "ROW", source_row: 38, client_name: "Delvin", loan_number: "109586-1", amount_usd: 19.25}]}};
        return {message: {credit_pending_usd: 19.25}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
const frm = {doc: {name: "CN-COMP-2026-00450"}, is_dirty: () => false, reload_doc: async () => {reloads++;}};
(async () => {
    context.cn_reconcile_reclassification(frm);
    values.deposit = "DEP-8-2025-0010";
    await dialog.fields.find(f => f.fieldname === "deposit").onchange();
    const option = dialog.fields.find(f => f.fieldname === "detail_row").options[1];
    await dialog.primary_action({deposit: values.deposit, detail_row: option,
        commitment_date: "2026-11-30", reason: "Reclasificación verificada"});
    assert.equal(calls[1].args.detail_row, "ROW");
    assert.equal(calls[1].args.item_modified, "I1");
    assert.equal(calls[1].args.deposit_modified, "D1");
    assert.equal(reloads, 1);
    assert.equal(messages.length, 0);
    console.log("OK: reclassification dialog previews and submits the selected verified row.");
})().catch(error => {console.error(error); process.exitCode = 1;});
