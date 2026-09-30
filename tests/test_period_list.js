const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const directory = path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period");
const meta = JSON.parse(fs.readFileSync(path.join(directory, "cn_reconciliation_period.json"), "utf8"));
const status = meta.fields.find(field => field.fieldname === "status");
assert.equal(status.in_list_view, 1);
const states = Object.fromEntries(meta.states.map(state => [state.title, state.color]));
assert.deepEqual(Object.keys(states).sort(), status.options.split("\n").sort());
assert.equal(meta.states.length, Object.keys(states).length);
assert.equal(states.Borrador, "Gray");
for (const title of ["Cobranza cargada", "Detalle empresa cargado", "Deduccion conciliada"]) {
    assert.equal(states[title], "Blue"); // First reconciliation does not imply cash received.
}
for (const title of ["Historico pendiente", "Historico parcial"]) assert.equal(states[title], "Orange");
assert.equal(states["Historico con excedente"], "Red");
for (const title of ["Deposito conciliado", "Historico conciliado"]) assert.equal(states[title], "Green");
assert.equal(states.Cerrado, "Purple");
const context = vm.createContext({frappe: {listview_settings: {}}});
vm.runInContext(fs.readFileSync(path.join(directory, "cn_reconciliation_period_list.js"), "utf8"), context);
assert.ok(context.frappe.listview_settings[meta.name].add_fields.includes("status"));
console.log("OK: status column, complete color mapping and status fetch for personalized lists.");
