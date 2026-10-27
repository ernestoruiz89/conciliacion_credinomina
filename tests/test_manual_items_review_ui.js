const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({__: text => text, frappe: {
    query_reports: {}, utils: {escape_html: text => text.replaceAll('"', "&quot;")},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/report/revision_de_partidas_manuales/revision_de_partidas_manuales.js", "utf8"), context);
const settings = context.frappe.query_reports["Revision de Partidas Manuales"];
assert.equal(settings.filters.find(field => field.fieldname === "show_details").default, 0);
let handler, applied;
const wrapper = {
    off(event, selector) {assert.equal(selector, ".manual-item-links"); handler = null; return this;},
    on(event, selector, callback) {handler = callback; return this;},
};
const report = {page: {wrapper}, set_filter_value(filters) {applied = filters;}};
settings.onload(report);
settings.onload(report);
let prevented = false;
handler.call({dataset: {item: "CN-COMP-1"}}, {preventDefault() {prevented = true;}});
assert.ok(prevented);
assert.deepEqual(JSON.parse(JSON.stringify(applied)), {item: "CN-COMP-1", show_details: 1});
const html = settings.formatter("", null, {fieldname: "details"}, {item: 'M" onclick="bad'}, () => "default");
assert.ok(html.includes('data-item="M&quot; onclick=&quot;bad"'));
assert.equal(settings.formatter("", null, {fieldname: "item"}, {item: "M"}, () => "default"), "default");
console.log("OK: summary default and scoped detail navigation without duplicate handlers.");
