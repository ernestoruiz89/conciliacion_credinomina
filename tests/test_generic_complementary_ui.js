const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const context = vm.createContext({__: text => text, frappe: {ui: {form: {on() {}}}}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const split = vm.runInContext("splitRemittanceTargetsForDetail", context);
function form(amount) {
    return {doc: {targets: [{name: "T", complementary_item: "ONE", amount_usd: amount, employer: "LALA"}]},
        add_child(table, values) { assert.equal(table, "targets"); this.doc.targets.push({name: "T" + this.doc.targets.length, ...values}); }};
}
const row = {name: "ANA", client_name: "Ana", source_row: 2, employer: "LALA"};
for (const sign of [1, -1]) {
    const frm = form(sign * 500);
    assert.equal(split(frm, row, [{target_id: "T", linked: 1, amount_usd: sign * 200}]), "");
    assert.equal(frm.doc.targets.length, 2);
    assert.equal(frm.doc.targets[0].detail_row, "ANA");
    assert.equal(frm.doc.targets[0].amount_usd, sign * 200);
    assert.equal(frm.doc.targets[1].amount_usd, sign * 300);
    assert.equal(frm.doc.targets[1].detail_row, "");
    assert.equal(split(frm, {...row, name: "LUIS", employer: "CBC"}, [{target_id: "T1", linked: 1, amount_usd: sign * 300}]), "");
    assert.equal(frm.doc.targets[1].detail_row, "LUIS");
    assert.equal(frm.doc.targets[1].employer, "CBC");
    assert.equal(frm.doc.targets.reduce((total, target) => total + target.amount_usd, 0), sign * 500);
    assert.ok(frm.doc.targets.every(target => target.complementary_item === "ONE"));
}
for (const amount of [500.01, -200, 0]) {
    const frm = form(500), before = JSON.stringify(frm.doc);
    assert.ok(split(frm, row, [{target_id: "T", linked: 1, amount_usd: amount}]));
    assert.equal(JSON.stringify(frm.doc), before);
}
const frm = form(50);
delete frm.doc.targets[0].complementary_item;
frm.doc.targets[0].historical_application = "CORE";
assert.ok(split(frm, row, [{target_id: "T", linked: 1, amount_usd: 20}]));
console.log("OK: shared complementary amount split by row, signed amounts and validation without financial writes.");
