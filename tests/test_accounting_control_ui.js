const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let handler, message;
const escape = value => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const context = vm.createContext({__: text => text, $: element => ({attr: () => element.description}), frappe: {
    query_reports: {}, datetime: {month_start: () => "2026-10-01"}, utils: {escape_html: escape}, msgprint: value => {message = value;},
}});
vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/report/control_mensual_de_movimientos_contables/control_mensual_de_movimientos_contables.js"), "utf8"), context);
const report = context.frappe.query_reports["Control Mensual de Movimientos Contables"];
const description = 'REGISTRAMOS RECLASIFICACION "a gasto"\n<script>alert(1)</script> ' + "Texto completo ".repeat(1000);
const formatted = report.formatter(description, {}, {fieldname: "description"}, {}, () => "default");
assert.ok(formatted.includes(escape(description)));
assert.ok(!formatted.includes("<script>"));
report.onload({page: {wrapper: {off() {}, on: (_event, _selector, callback) => {handler = callback;}}}});
handler.call({description}, {preventDefault() {}});
assert.ok(message.message.includes(escape(description)));
assert.equal(report.formatter(null, {}, {fieldname: "debit_nio"}, {}, () => "0"), "—");
assert.equal(report.filters.find(field => field.fieldname === "summary").default, 0);
console.log("OK: descripción completa sin recortes, modal seguro, valores no disponibles y filtros.");
