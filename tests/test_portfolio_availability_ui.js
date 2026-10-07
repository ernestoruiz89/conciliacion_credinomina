const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_credit_portfolio_snapshot/cn_credit_portfolio_snapshot.js"), "utf8");
let handlers, rpc, calls = [], alerts = [], messages = [];
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on: (_, events) => {handlers = events;}}},
    call: args => {calls.push(args); return rpc(args);},
    show_alert: msg => alerts.push(msg), msgprint: msg => messages.push(msg),
}});
vm.runInContext(source, context);
function form() {
    return {doc: {name: "CUT", doctype: "CN Credit Portfolio Snapshot", disabled: 0,
        source_file: "cut.xlsx", notes: "", modified: "OLD", modified_by: "USER", __unsaved: 0,
        rows: Array(10000).fill({credit_number: "123"})},
        is_new: () => false, is_dirty() {return !!this.doc.__unsaved;},
        add_custom_button() {}, set_df_property(_, __, value) {this.readOnly = value;},
        disable_save(setDirty) {assert.equal(setDirty, true); this.savingDisabled = true;}, enable_save() {this.savingDisabled = false;},
        refresh_header() {}, refresh_field() {},
        save() {throw new Error("Full save must not run");},
        reload_doc() {throw new Error("Full reload must not run");},
    };
}
(async () => {
    let frm = form(); handlers.refresh(frm);
    let complete;
    rpc = () => new Promise(resolve => {complete = resolve;});
    frm.doc.disabled = 1; frm.doc.__unsaved = 1;
    const pending = handlers.disabled(frm);
    await handlers.disabled(frm);
    assert.equal(calls.length, 1);
    assert.equal(frm.savingDisabled, true);
    assert.deepEqual(JSON.parse(JSON.stringify(calls[0].args)), {snapshot_name: "CUT", disabled: 1, modified: "OLD"});
    assert.ok(JSON.stringify(calls[0].args).length < 100);
    complete({message: {disabled: 1, modified: "NEW", modified_by: "USER"}});
    await pending;
    assert.equal(frm.doc.__unsaved, 0);
    assert.equal(frm.doc.modified, "NEW");
    assert.equal(frm.doc.rows.length, 10000);
    assert.equal(frm.readOnly, 0);
    assert.equal(frm.savingDisabled, false);
    assert.equal(alerts.length, 1);
    // Header edits are neither sent nor incorrectly marked saved.
    frm.doc.notes = "Cambios pendientes"; frm.doc.disabled = 0; frm.doc.__unsaved = 1;
    rpc = async () => ({message: {disabled: 0, modified: "NEWER", modified_by: "USER"}});
    await handlers.disabled(frm);
    assert.equal(frm.doc.__unsaved, 1);
    assert.equal(frm.doc.notes, "Cambios pendientes");
    assert.equal(calls[1].args.modified, "NEW");
    rpc = async () => {throw new Error("Disconnected");};
    frm.doc.disabled = 1;
    await handlers.disabled(frm);
    assert.equal(frm.doc.__unsaved, 1);
    assert.equal(frm.doc.modified, "NEWER");
    assert.equal(alerts.length, 2);
    assert.ok(messages[0].includes("Recargue"));
    console.log("OK: constant-size toggle request, no full save/reload, concurrency and unsaved edits preserved.");
})().catch(error => {console.error(error); process.exitCode = 1;});
