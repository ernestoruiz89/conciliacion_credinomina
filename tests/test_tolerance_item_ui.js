const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {};
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on: (dt, events) => {handlers[dt] = events;}}}, listview_settings: {},
}});
const base = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item");
for (const file of ["cn_complementary_item.js", "cn_complementary_item_list.js"]) {
    vm.runInContext(fs.readFileSync(path.join(base, file), "utf8"), context);
}
const events = handlers["CN Complementary Item"];
const properties = {};
let disabled = false, headline;
const frm = {doc: {category: "Diferencia por tolerancia", docstatus: 1, status: "Vigente"},
    fields: [{df: {fieldname: "category", read_only: 0}}, {df: {fieldname: "voucher", read_only: 0}},
        {df: {fieldname: "amount_usd", read_only: 1}}],
    set_df_property: (field, property, value) => {properties[field + ":" + property] = value;},
    disable_save() {disabled = true;}, enable_save() {disabled = false;}, get_perm: () => true,
    toggle_display() {}, trigger: name => events[name](frm), is_new: () => true,
    dashboard: {set_headline_alert(message) {headline = message;}, clear_headline() {headline = "";}},
};
events.refresh(frm);
assert.equal(disabled, true);
assert.equal(properties["voucher:read_only"], 1);
assert.equal(properties["category:read_only"], 1);
assert.ok(headline.includes("No requiere asiento"));
assert.ok(properties["category:options"].includes("Diferencia por tolerancia"));
frm.doc = {category: "Cobranza administrativa", docstatus: 0, voucher: ""};
events.refresh(frm);
assert.equal(disabled, false);
assert.equal(properties["category:read_only"], 0);
assert.equal(properties["voucher:read_only"], 0);
assert.equal(properties["amount_usd:read_only"], 1);
assert.ok(!properties["category:options"].includes("Diferencia por tolerancia"));
assert.ok(headline.includes("Pendiente de registro contable"));
const list = context.frappe.listview_settings["CN Complementary Item"];
assert.equal(list.get_indicator({category: "Diferencia por tolerancia", status: "Vigente"})[0], "Vigente · tolerancia");
assert.equal(list.get_indicator({category: "Diferencia por tolerancia", status: "Revertido"})[0], "Revertido");
assert.equal(list.get_indicator({category: "Cobranza administrativa", accounting_status: "Pendiente de registro"})[0], "Pendiente de registro");
console.log("OK: automatic tolerance category is read-only, not offered for manual creation, and separate from pending accounting.");
