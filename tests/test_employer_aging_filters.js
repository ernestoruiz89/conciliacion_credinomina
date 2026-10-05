const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/report");
const context = vm.createContext({__: text => text,
    frappe: {query_reports: {}, datetime: {get_today: () => "2026-09-30"}}});
for (const name of ["antiguedad_de_saldos", "antiguedad_de_saldos_por_empresa"]) {
    vm.runInContext(fs.readFileSync(path.join(root, name, name + ".js"), "utf8"), context);
}
assert.equal(JSON.stringify(context.frappe.query_reports["Antiguedad de Saldos"].filters),
    JSON.stringify(context.frappe.query_reports["Antiguedad de Saldos por Empresa"].filters));
const detailed = JSON.parse(fs.readFileSync(path.join(root, "antiguedad_de_saldos/antiguedad_de_saldos.json"), "utf8"));
const grouped = JSON.parse(fs.readFileSync(path.join(root, "antiguedad_de_saldos_por_empresa/antiguedad_de_saldos_por_empresa.json"), "utf8"));
assert.deepEqual(grouped.roles, detailed.roles);
assert.equal(grouped.ref_doctype, detailed.ref_doctype);
const balance = context.frappe.query_reports["Antiguedad de Saldos"].filters.find(f => f.fieldname === "balance_type");
assert.equal(balance.default, "CxC total");
assert.equal(balance.options.split("\n").slice(0, 3).join("|"),
    "CxC total|Aplicado pendiente de depósito|CxC por ajustes");
console.log("OK: same aging filters, defaults and report roles.");
