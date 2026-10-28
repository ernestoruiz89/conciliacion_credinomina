const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8");
const routes = [], messages = [];
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}},
    set_route: (...args) => routes.push(args), msgprint: message => messages.push(message),
}});
vm.runInContext(source, context);
assert.match(source, /add_custom_button\(__\("Ver Partidas Complementarias"\),\s*\(\) => showRemittanceComplementaryItems\(frm\), __\("Conciliación"\)\)/);
context.showRemittanceComplementaryItems({doc: {name: "DEP-8-2025-0010", employer: "MULTIPERFILES"}});
assert.equal(routes.length, 1);
assert.equal(routes[0][0], "List");
assert.equal(routes[0][1], "CN Complementary Item");
assert.deepEqual({...routes[0][2]}, {employer: "MULTIPERFILES"});
context.showRemittanceComplementaryItems({doc: {employer: ""}});
assert.equal(routes.length, 1);
assert.equal(messages.length, 1);
console.log("OK: complementary navigation applies only the deposit company and requires a company.");
