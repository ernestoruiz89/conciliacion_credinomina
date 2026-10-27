const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let dialog, rendered, requests = [], fail = false;
const escape = text => text.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const payload = {
    summary: {item: "CN-COMP-1", amount_usd: 10, description: "<script>bad</script>"},
    summary_columns: [
        {fieldname: "item", label: "Partida", fieldtype: "Link", options: "CN Complementary Item"},
        {fieldname: "amount_usd", label: "Importe", fieldtype: "Currency"},
        {fieldname: "description", label: "Descripción", fieldtype: "Data"},
    ],
    columns: [{fieldname: "related_document", label: "Documento", fieldtype: "Dynamic Link", options: "related_doctype"}],
    rows: [{related_document: "DEP-1", related_doctype: "CN Remittance Allocation"}],
    message: "Detalle",
};
const context = vm.createContext({__: text => text, frappe: {
    query_reports: {}, utils: {escape_html: escape, get_form_link: (type, name) => `/app/${type}/${name}`},
    format: value => String(value),
    async call(request) {requests.push(request); if (fail) throw Error("network"); return {message: payload};},
    ui: {Dialog: class {
        constructor(options) {dialog = this; this.options = options; this.fields_dict = {detail: {$wrapper: {html(value) {rendered = value;}}}};}
        show() {this.shown = true;}
        disable_primary_action() {this.disabled = true;}
        enable_primary_action() {this.disabled = false;}
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/report/revision_de_partidas_manuales/revision_de_partidas_manuales.js", "utf8"), context);
const settings = context.frappe.query_reports["Revision de Partidas Manuales"];
assert.equal(settings.filters.find(field => field.fieldname === "show_details").default, 0);
let handler;
const wrapper = {
    off(event, selector) {assert.equal(selector, ".manual-item-links"); handler = null; return this;},
    on(event, selector, callback) {handler = callback; return this;},
};
const report = {page: {wrapper}, filters: {employer: "E", item: "", show_details: 0},
    set_filter_value() {assert.fail("Opening a modal must not change report filters");},
    refresh() {assert.fail("Opening a modal must not reload the report");}};
settings.onload(report);
settings.onload(report);
(async () => {
const before = JSON.stringify(report.filters);
let prevented = false;
await handler.call({dataset: {item: "CN-COMP-1"}}, {preventDefault() {prevented = true;}});
assert.ok(prevented);
assert.ok(dialog.shown);
assert.equal(dialog.options.size, "extra-large");
assert.equal(requests[0].method, "credinomina_reconciliation.manual_items_review.get_item_details");
assert.deepEqual(JSON.parse(JSON.stringify(requests[0].args)), {item: "CN-COMP-1"});
assert.equal(JSON.stringify(report.filters), before);
assert.ok(rendered.includes("DEP-1"));
assert.ok(rendered.includes('target="_blank"'));
assert.ok(rendered.includes("&lt;script&gt;bad&lt;/script&gt;"));
assert.ok(!rendered.includes("<script>"));
fail = true;
await dialog.options.primary_action();
assert.ok(rendered.includes("reintentar"));
assert.equal(dialog.disabled, false);
fail = false;
await dialog.options.primary_action();
assert.ok(rendered.includes("DEP-1"));
assert.equal(JSON.stringify(report.filters), before);
assert.ok(settings.render_details({}).includes("No hay detalle visible"));
const html = settings.formatter("", null, {fieldname: "details"}, {item: 'M" onclick="bad'}, () => "default");
assert.ok(html.includes('data-item="M&quot; onclick=&quot;bad"'));
assert.equal(settings.formatter("", null, {fieldname: "item"}, {item: "M"}, () => "default"), "default");
console.log("OK: detail modal, unchanged report, escaped evidence, links and retry.");
})().catch(error => {console.error(error); process.exitCode = 1;});
