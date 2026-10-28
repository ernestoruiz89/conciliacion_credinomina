const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8");
const routes = [], messages = [], calls = [];
let dirty = false, items = ["CN-COMP-2026-00550", "CN-COMP-2026-00551"];
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}},
    set_route: (...args) => routes.push(args), msgprint: message => messages.push(message),
    call: async args => {calls.push(args); return {message: items};},
}});
vm.runInContext(source, context);
assert.match(source, /add_custom_button\(__\("Ver Partidas Complementarias"\),\s*\(\) => showRemittanceComplementaryItems\(frm\), __\("Conciliación"\)\)/);
const frm = {doc: {name: "DEP-8-2025-0010", employer: "MULTIPERFILES"}, is_dirty: () => dirty};
(async () => {
    await context.showRemittanceComplementaryItems(frm);
    assert.equal(calls[0].args.remittance_name, "DEP-8-2025-0010");
    assert.equal(routes.length, 1);
    assert.equal(routes[0][0], "List");
    assert.equal(routes[0][1], "CN Complementary Item");
    assert.deepEqual(JSON.parse(JSON.stringify(routes[0][2])), {name: ["in", items]});
    items = [];
    await context.showRemittanceComplementaryItems(frm);
    assert.equal(routes.length, 1);
    assert.equal(messages.length, 1);
    dirty = true;
    await context.showRemittanceComplementaryItems(frm);
    assert.equal(calls.length, 2);
    assert.equal(messages.length, 2);
    console.log("OK: navigation uses only linked items, handles empty results and requires saved changes.");
})().catch(error => {console.error(error); process.exitCode = 1;});
