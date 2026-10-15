const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const esc = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const context = vm.createContext({
    __: (text, values) => values ? text.replace(/\{(\d+)\}/g, (_, index) => values[index]) : text,
    format_currency: value => `USD ${Number(value).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: esc}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
const wrapper = {html(value) {this.content = value;}, empty() {this.content = "";},
    off() {this.change = null;}, on(_event, _selector, handler) {this.change = handler;}};
const frm = {fields_dict: {quality_html: {$wrapper: wrapper}}, doc: {reconciliation_mode: "Operativa", collection_rows: [{
    client_name: '<script>bad()</script>', loan_number: '123-1', quality_basis: "Cobranza",
    quality_status: "Conforme según cobranza", expected_usd: 100, quality_expected_usd: 100, quality_difference_usd: 0,
    applied_usd: 100, deduction_status: "Pendiente de detalle",
}]}};
context.refreshApplicationQuality(frm);
assert.ok(wrapper.content.includes("Conforme según cobranza"));
assert.ok(!wrapper.content.includes("Pendiente de detalle"));
assert.ok(wrapper.content.includes("Cobranza US$"));
assert.ok(wrapper.content.includes("Conforme no significa depositado"));
assert.ok(wrapper.content.includes("indicator-pill green"));
assert.ok(!wrapper.content.includes("<script>"));
assert.ok(wrapper.content.includes("&lt;script>"));
frm.doc.collection_rows[0].applied_usd = 80;
context.refreshApplicationQuality(frm);
assert.ok(wrapper.content.includes("indicator-pill orange"));
frm.doc.collection_rows = [
    {client_name: 'Faltante', expected_usd: 100, applied_usd: 80},
    {client_name: 'Excedente', expected_usd: 100, applied_usd: 120},
    {client_name: 'Exacto', expected_usd: 100, applied_usd: 100},
    {client_name: 'Sin conversión', expected_nio: 100, applied_usd: 1},
    {client_name: 'Precisión', expected_usd: 100, applied_usd: 100.000001},
];
const originalRows = JSON.stringify(frm.doc.collection_rows);
context.refreshApplicationQuality(frm);
wrapper.change({currentTarget: {value: 'differences'}});
assert.match(wrapper.content, /Mostrando 2 de 5 filas/);
assert.match(wrapper.content, /<td>Faltante<\/td>/);
assert.match(wrapper.content, /<td>Excedente<\/td>/);
assert.ok(!wrapper.content.includes('<td>Exacto</td>'));
assert.ok(!wrapper.content.includes('<td>Sin conversión</td>'));
assert.ok(!wrapper.content.includes('<td>Precisión</td>'));
context.refreshApplicationQuality(frm);
assert.match(wrapper.content, /value="differences" selected/);
for (const [filter, count, name] of [['shortfall', 1, 'Faltante'], ['excess', 1, 'Excedente'],
    ['missing', 1, 'Sin conversión'], ['conforming', 2, 'Exacto'], ['all', 5, 'Precisión']]) {
    wrapper.change({currentTarget: {value: filter}});
    assert.ok(wrapper.content.includes(`Mostrando ${count} de 5 filas`));
    assert.ok(wrapper.content.includes(`<td>${name}</td>`));
}
assert.equal(JSON.stringify(frm.doc.collection_rows), originalRows);
frm.doc.collection_rows = [{expected_usd: 100, applied_usd: 100}];
wrapper.change({currentTarget: {value: 'differences'}});
assert.match(wrapper.content, /No hay filas que coincidan con el filtro/);
frm.doc.reconciliation_mode = "Historica";
context.refreshApplicationQuality(frm);
assert.equal(wrapper.content, "");
frm.doc.no_collection = 1;
context.refreshApplicationQuality(frm);
assert.match(wrapper.content, /La conciliación 1 no aplica/);
assert.ok(!wrapper.content.includes('data-quality="filter"'));
frm.doc.no_collection = 0;
console.log("PASS application quality UI: difference filters, counts, refresh persistence, unchanged data, colors, escaping, historical hidden");

frm.fields_dict.legacy_detail_html = {$wrapper: wrapper};
frm.doc.legacy_employer_detail = JSON.stringify({parent: {employer_response_file: 'javascript:bad()'},
    rows: [{client_name: '<script>bad()</script>', deducted_usd: 80}], review: ['<img src=x>']});
context.refreshLegacyEmployerDetail(frm);
assert.ok(wrapper.content.includes('Auditoría de solo lectura'));
assert.ok(!wrapper.content.includes('href="javascript:'));
assert.ok(!wrapper.content.includes('<script>'));
assert.ok(!wrapper.content.includes('<img src=x>'));
frm.doc.legacy_employer_detail = '';
context.refreshLegacyEmployerDetail(frm);
assert.equal(wrapper.content, '');
