const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const esc = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const context = vm.createContext({
    __: text => text, format_currency: value => `USD ${Number(value).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: esc}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
const wrapper = {html(value) {this.content = value;}, empty() {this.content = "";}};
const frm = {fields_dict: {quality_html: {$wrapper: wrapper}}, doc: {reconciliation_mode: "Operativa", collection_rows: [{
    client_name: '<script>bad()</script>', loan_number: '123-1', quality_basis: "Cobranza",
    quality_status: "Conforme según cobranza", quality_expected_usd: 100, quality_difference_usd: 0,
    applied_usd: 100, deduction_status: "Pendiente de detalle",
}]}};
context.refreshApplicationQuality(frm);
assert.ok(wrapper.content.includes("Conforme según cobranza"));
assert.ok(wrapper.content.includes("Pendiente de detalle"));
assert.ok(wrapper.content.includes("Conforme no significa depositado"));
assert.ok(wrapper.content.includes("indicator-pill green"));
assert.ok(!wrapper.content.includes("<script>"));
assert.ok(wrapper.content.includes("&lt;script>"));
frm.doc.collection_rows[0].quality_status = "Aplicación insuficiente";
context.refreshApplicationQuality(frm);
assert.ok(wrapper.content.includes("indicator-pill orange"));
frm.doc.reconciliation_mode = "Historica";
context.refreshApplicationQuality(frm);
assert.equal(wrapper.content, "");
console.log("PASS application quality UI: basis, deduction warning, colors, escaped data, historical hidden");
