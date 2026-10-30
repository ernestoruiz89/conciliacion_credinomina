const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], dialogs = [], shown = [], messages = [], handlers = {};
const context = vm.createContext({__: text => text, window: {credinominaShowRelatedPending: data => shown.push(data)}, frappe: {
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    msgprint: message => messages.push(message), require: async () => {},
    call: args => new Promise(resolve => calls.push({args, resolve})),
    ui: {form: {on(dt, events) {handlers[dt] = events;}}, Dialog: function(options) {
        Object.assign(this, options);
        this.show = () => {};
        this.hide = () => this.onhide();
        this.disable_primary_action = () => {this.disabled = true;};
        this.enable_primary_action = () => {this.disabled = false;};
        dialogs.push(this);
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js", "utf8"), context);
const frm = {doc: {name: "ITEM", source_client_name: "RONAL FLORE PERE", employer: "IMMSA"},
    is_new: () => false, is_dirty: () => false, fields_dict: {}};
const tick = () => new Promise(resolve => setImmediate(resolve));
const suggestions = {needs_client_selection: true, employer: "IMMSA", query_name: "<RONAL>", candidates: [
    {client: "3253", client_number: "3253", client_name: "RONALD FLORES PEREZ", similarity: 92},
    {client: "2", client_number: "2", client_name: "RONALDO FLORES PEREZ", similarity: 89}]};
(async () => {
    const pending = handlers["CN Complementary Item"].view_related_pending(frm);
    calls[0].resolve({message: suggestions}); await tick();
    assert.equal(shown.length, 0, "Never auto-open the first similar client");
    const dialog = dialogs[0];
    assert.ok(dialog.fields[0].options.includes("&lt;RONAL>"));
    const select = dialog.fields.find(field => field.fieldname === "client");
    assert.equal(select.options[0].value, "", "Selection must be explicit");
    assert.equal(select.reqd, 1);
    await dialog.primary_action({client: "FORGED"}); assert.equal(calls.length, 1);
    const chosen = dialog.primary_action({client: "3253"});
    await dialog.primary_action({client: "3253"}); assert.equal(calls.length, 2);
    assert.equal(calls[1].args.args.selected_client, "3253");
    calls[1].resolve({message: {client: "3253", context: {complementary_item: "ITEM", client_choice: "3253"}}});
    await chosen; await pending;
    assert.equal(shown[0].context.client_choice, "3253");
    assert.equal(frm.doc.client_name, undefined);
    assert.equal(frm.doc.client_number, undefined);
    assert.equal(frm._cn_related_pending_loading, false);
    const cancelled = handlers["CN Complementary Item"].view_related_pending(frm);
    calls[2].resolve({message: suggestions}); await tick(); dialogs[1].hide(); await cancelled;
    assert.equal(shown.length, 1, "Cancel must not select a client");
    const empty = handlers["CN Complementary Item"].view_related_pending(frm);
    calls[3].resolve({message: {...suggestions, candidates: [], reason: "<No coincidencias>"}}); await empty;
    assert.ok(messages.at(-1).includes("&lt;No coincidencias>"));
    console.log("OK: explicit client suggestions, confirmed navigation, duplicate protection, safe text and cancellation without edits.");
})().catch(error => {console.error(error); process.exitCode = 1;});
