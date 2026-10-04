const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const context = vm.createContext({__: value => value, frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_operativo/estado_de_cuenta_operativo.js"), "utf8"), context);
const filters = context.frappe.query_reports["Estado de Cuenta Operativo"].filters;
const type = filters.find(field => field.fieldname === "position_type");
assert.equal(type.label, "Tipo");
assert.equal(type.options, "\nCobranza\nAplicación\nPartida complementaria");
assert.equal(filters.filter(field => field.fieldname === "position_type").length, 1);
const status = filters.find(field => field.fieldname === "operational_status");
assert.equal(status.label, "Estado de conciliación");
assert.equal(status.fieldtype, "Autocomplete");
for (const value of ["Conciliado", "Pendiente", "Documentado", "Parcial", "No conciliatoria", "Sin conversión US$"]) {
    assert.ok(status.options.includes(value));
}
assert.ok(status.description.includes("estado exacto"));
assert.ok(filters.some(field => field.fieldname === "only_open"));
console.log("OK: operational statement type and reconciliation-state filters.");
