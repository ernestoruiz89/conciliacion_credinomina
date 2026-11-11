const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let events, dirty = false, fresh = false;
const actions = {}, routes = [], messages = [];
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on(_doctype, handlers) {events = handlers;}}},
    set_route: (...args) => routes.push(args), msgprint: text => messages.push(text),
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_client/cn_client.js", "utf8"), context);
const frm = {doc: {name: "00123", client_number: "00123", employer: "INDENICSA"},
    is_new: () => fresh, is_dirty: () => dirty, add_custom_button(label, action) {actions[label] = action;}};
events.refresh(frm);
assert.equal(typeof actions["Ver Estado de Cuenta Operativo"], "function");
assert.equal(typeof actions["Ver partidas vinculadas"], "function");
actions["Ver Estado de Cuenta Operativo"]();
assert.equal(routes[0][0], "query-report");
assert.equal(routes[0][1], "Estado de Cuenta Operativo");
assert.equal(routes[0][2].client_number, "00123");
assert.equal(routes[0][2].employer, "INDENICSA");
assert.equal(routes[0][2].position_type, "Aplicaciones, depósitos y partidas");
assert.equal(routes[0][2].detailed_columns, 0);
assert.equal(routes[0][2].only_open, 1);
dirty = true;
actions["Ver Estado de Cuenta Operativo"]();
assert.equal(routes.length, 1);
assert.equal(messages.length, 1);
delete actions["Ver Estado de Cuenta Operativo"];
fresh = true;
events.refresh(frm);
assert.equal(actions["Ver Estado de Cuenta Operativo"], undefined);
fresh = false;
frm.doc.client_number = "";
events.refresh(frm);
assert.equal(actions["Ver Estado de Cuenta Operativo"], undefined, "Never open an unfiltered statement");
console.log("Client operational statement button: passed");
