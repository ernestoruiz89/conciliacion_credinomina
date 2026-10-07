const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const handlers = {}, events = {};
const context = vm.createContext({
    __: value => value, setTimeout: callback => callback(),
    frappe: {ui: {form: {on: (name, methods) => {handlers[name] = methods;}}},
        model: {can_create: () => true, can_submit: () => true}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
// Isolate the form lifecycle from unrelated overview rendering and requests.
for (const name of ["loadPayingCompanies", "renderRemittanceOverview", "renderRemittanceAllocations",
    "renderRemittanceDistribution", "toggleRemittanceDetailActions", "toggleApplicationDetailAction",
    "addCompanyCreditButton"]) context[name] = () => {};

let selected = [], additions = 0;
const grid = {
    df: {}, get_selected_children: () => selected,
    add_custom_button(label) {
        assert.ok(this.custom_buttons, "Grid controls are unavailable during setup");
        if (!this.custom_buttons[label]) {
            additions++;
            this.custom_buttons[label] = {toggleClass(_name, hidden) {this.hidden = hidden;}};
        }
        return this.custom_buttons[label];
    },
};
const frm = {fields_dict: {detail_rows: {grid}}, doc: {docstatus: 1, detail_rows: [], targets: []},
    is_new: () => false, get_perm: () => true, set_query() {}, toggle_display() {}, add_custom_button() {}};
const form = handlers["CN Remittance Allocation"];
form.setup(frm);
assert.equal(additions, 0);
form.refresh(frm); // A hidden/unrendered grid must not break the rest of the form.
assert.equal(additions, 0);

grid.custom_buttons = {};
grid.grid_buttons = {};
grid.wrapper = {off(event) {delete events[event]; return this;},
    on(event, _selector, callback) {events[event] = callback; return this;}};
form.refresh(frm);
assert.equal(additions, 1);
assert.equal(frm.bulkClientCreditButton.hidden, true);
const change = () => events["change.cnBulkClientCredits"]();
selected = [{name: "A", pending_usd: 10}];
change();
assert.equal(frm.bulkClientCreditButton.hidden, true);
selected.push({name: "B", pending_usd: 20});
change();
assert.equal(frm.bulkClientCreditButton.hidden, false);
form.refresh(frm);
assert.equal(additions, 1, "Refreshing must reuse the button");
assert.equal(Object.keys(events).length, 1, "Refreshing must replace the selection handler");
selected[1].pending_usd = 0;
change();
assert.equal(frm.bulkClientCreditButton.hidden, true);
selected[1].pending_usd = 20;
frm.doc.docstatus = 0;
form.refresh(frm);
assert.equal(frm.bulkClientCreditButton.hidden, true);
console.log("OK: setup before grid initialization, refresh, selection visibility and no duplicate buttons.");
