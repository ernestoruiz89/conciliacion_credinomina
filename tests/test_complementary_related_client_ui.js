const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const directory = "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/";
const schema = JSON.parse(fs.readFileSync(directory + "cn_complementary_item.json", "utf8"));
const field = schema.fields.find(row => row.fieldname === "view_related_pending");
assert.equal(schema.field_order[schema.field_order.indexOf("client_name") + 1], field.fieldname);
assert.equal(field.fieldtype, "Button");
assert.equal(field.allow_on_submit, 1);
for (const key of ["client_name", "client_number", "loan_number"]) {
    assert.equal(vm.runInNewContext(field.depends_on.slice(5), {doc: {[key]: "value"}}), true);
}
assert.equal(vm.runInNewContext(field.depends_on.slice(5), {doc: {client_name: "  "}}), false);
const handlers = {}, messages = [], calls = [], shown = [];
let complete, requireComplete, dirty = false, fresh = false, disabled = false, fail = false;
const context = vm.createContext({__: text => text, window: {credinominaShowRelatedPending: result => shown.push(result)},
    frappe: {ui: {form: {on(doctype, events) {handlers[doctype] = events;}}},
        msgprint: text => messages.push(text), call(args) {
            calls.push(args);
            if (fail) return Promise.reject(Error("Identidad ambigua"));
            return new Promise(resolve => {complete = resolve;});
        }, require: () => new Promise(resolve => {requireComplete = resolve;})}});
vm.runInContext(fs.readFileSync(directory + "cn_complementary_item.js", "utf8"), context);
const frm = {doc: {name: "ITEM", modified: "V1", loan_number: "13682-1"},
    is_new: () => fresh, is_dirty: () => dirty,
    fields_dict: {view_related_pending: {$input: {prop(_name, value) {disabled = value;}}}}};
const click = () => handlers["CN Complementary Item"].view_related_pending(frm);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    dirty = true; await click(); assert.equal(calls.length, 0);
    dirty = false; fresh = true; await click(); assert.equal(calls.length, 0); fresh = false;
    let pending = click(); await click(); assert.equal(calls.length, 1);
    assert.equal(disabled, true);
    assert.deepEqual(JSON.parse(calls[0].args.context), {complementary_item: "ITEM"});
    complete({message: {employer: "LALA", client: "7593", applications: [], rows: []}});
    await tick(); requireComplete(); await pending;
    assert.equal(shown.length, 1);
    assert.equal(shown[0].context.complementary_item, "ITEM");
    assert.equal(shown[0].client, "7593");
    assert.equal(disabled, false);
    pending = click(); frm.doc.name = "OTHER"; complete({message: {client: "STALE"}}); await pending;
    assert.equal(shown.length, 1, "Navigating to another document must discard stale results");
    pending = click(); complete({message: {client: "STALE2"}}); await tick();
    frm.doc.loan_number = "OTHER-LOAN"; requireComplete(); await pending;
    assert.equal(shown.length, 1, "Discard late results when identifiers change");
    fail = true; await assert.rejects(click(), /Identidad ambigua/);
    assert.equal(disabled, false); assert.equal(frm._cn_related_pending_loading, false);
    console.log("OK: inline button, all single identifiers, saved origin, duplicate prevention and stale/error recovery.");
})().catch(error => {console.error(error); process.exitCode = 1;});
