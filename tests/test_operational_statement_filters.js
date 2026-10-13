const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const context = vm.createContext({__: value => value, frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_operativo/estado_de_cuenta_operativo.js"), "utf8"), context);
const filters = context.frappe.query_reports["Estado de Cuenta Operativo"].filters;
const type = filters.find(field => field.fieldname === "position_type");
assert.equal(type.label, "Tipo");
assert.equal(type.options, "\nCobranza\nAplicación\nPartida complementaria\nCxC por ajuste");
assert.equal(filters.filter(field => field.fieldname === "position_type").length, 1);
const status = filters.find(field => field.fieldname === "operational_status");
assert.equal(status.label, "Estado de conciliación");
assert.equal(status.fieldtype, "Autocomplete");
for (const value of ["Conciliado", "Pendiente", "Parcial", "Sin conversión US$"]) {
    assert.ok(status.options.includes(value));
}
assert.ok(status.description.includes("estado exacto"));
assert.ok(!status.options.includes("Documentado"));
assert.ok(filters.find(f => f.fieldname === "treatment_status").options.includes("Documentado"));
assert.ok(filters.some(field => field.fieldname === "only_open"));
const report = context.frappe.query_reports["Estado de Cuenta Operativo"];
const shown = new Map();
let toggle;
report.onload({get_filter: name => ({get_value: () => name === 'reason_type' ? 'Por cancelación' : '',
    $wrapper: {toggle: value => shown.set(name, value)}}),
    page: {add_inner_button: (_name, callback) => {toggle = callback; return {text() {}};}}});
assert.equal(shown.get('reason_type'), true, 'Active classification filters remain visible');
assert.equal(shown.get('accounting_status'), false);
toggle();
assert.equal(shown.get('accounting_status'), true);
assert.match(report.formatter(null, 0, {fieldname:'complementary_pending_usd', fieldtype:'Currency'},
    {complementary_pending_usd:null}, () => '0.00'), /—/);
assert.equal(report.formatter(0, 0, {fieldname:'complementary_pending_usd', fieldtype:'Currency'},
    {complementary_pending_usd:0}, () => '0.00'), '0.00');
console.log("OK: operational statement type and reconciliation-state filters.");
