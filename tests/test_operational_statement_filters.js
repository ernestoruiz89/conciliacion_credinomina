const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const context = vm.createContext({__: value => value, frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_operativo/estado_de_cuenta_operativo.js"), "utf8"), context);
const filters = context.frappe.query_reports["Estado de Cuenta Operativo"].filters;
const cutoff = filters.find(field => field.fieldname === "cutoff_date");
assert.equal(cutoff.label, "Fecha de corte histórico");
assert.equal(cutoff.fieldtype, "Date");
assert.ok(!cutoff.default && !cutoff.reqd, 'Empty cutoff preserves the current statement');
const type = filters.find(field => field.fieldname === "position_type");
assert.equal(type.label, "Tipo");
assert.equal(type.options, "\nAplicaciones, depósitos y partidas\nCobranza\nAplicación\nDepósito con saldo pendiente\nPartida complementaria\nCxC por ajuste");
assert.equal(type.default, "Aplicaciones, depósitos y partidas");
assert.equal(filters.find(field => field.fieldname === "detailed_columns").default, 0);
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
const pending = filters.find(field => field.fieldname === "only_open");
assert.equal(pending.label, "Mostrar solo pendientes");
assert.equal(pending.fieldtype, "Check");
assert.equal(pending.default, 1);
const report = context.frappe.query_reports["Estado de Cuenta Operativo"];
const shown = new Map(filters.map(field => [field.fieldname, true]));
let toggle;
report.onload({get_filter: name => ({get_value: () => name === 'reason_type' ? 'Por cancelación' : '',
    $wrapper: {toggle: value => shown.set(name, value)}}),
    page: {add_inner_button: (_name, callback) => {toggle = callback; return {text() {}};}}});
assert.equal(shown.get('reason_type'), true, 'Active classification filters remain visible');
assert.equal(shown.get('accounting_status'), false);
assert.equal(shown.get('cutoff_date'), true, 'Historical cutoff is visible in the compact view');
assert.equal(shown.get('only_open'), true, 'Pending-only checkbox must be visible without expanding advanced filters');
toggle();
assert.equal(shown.get('accounting_status'), true);
toggle();
assert.equal(shown.get('only_open'), true, 'Collapsing advanced filters must not hide the unchecked pending-only checkbox');
assert.match(report.formatter(null, 0, {fieldname:'complementary_pending_usd', fieldtype:'Currency'},
    {complementary_pending_usd:null}, () => '0.00'), /—/);
assert.equal(report.formatter(0, 0, {fieldname:'complementary_pending_usd', fieldtype:'Currency'},
    {complementary_pending_usd:0}, () => '0.00'), '0.00');
assert.match(report.formatter('Total', 2, {fieldname:'client_name'}, {is_total_row:1}, () => 'Total'), /<strong>Total<\/strong>/);
assert.match(report.formatter(null, 2, {fieldname:'remitted_usd', fieldtype:'Currency'},
    {is_total_row:1, remitted_usd:null}, () => '0.00'), /<strong>.*—.*<\/strong>/);
console.log("OK: operational statement type and reconciliation-state filters.");
