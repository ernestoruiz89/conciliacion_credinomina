const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], dialogs = [], shown = [], handlers = {}, messages = [];
let choice = "", reloads = 0;
const context = vm.createContext({__: text => text, window: {credinominaShowRelatedPending: result => shown.push(result)}, frappe: {
    utils: {escape_html: String}, show_alert() {}, msgprint: message => messages.push(message), require: async () => {},
    call: args => new Promise(resolve => calls.push({args, resolve})),
    ui: {form: {on(dt, events) {handlers[dt] = events;}}, Dialog: function(options) {
        Object.assign(this, options); this.show = () => {}; this.hide = () => this.onhide();
        this.disable_primary_action = () => {}; this.enable_primary_action = () => {};
        this.get_value = () => choice;
        this.set_df_property = (name, key, value) => {this.fields.find(field => field.fieldname === name)[key] = value;};
        dialogs.push(this);
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js", "utf8"), context);
const frm = {doc: {name: "ITEM", modified: "V1", employer: "IMMSA", source_client_name: "RONAL FLORE PERE"},
    fields_dict: {}, is_new: () => false, is_dirty: () => false, async reload_doc() {
        reloads++; Object.assign(this.doc, {modified: "V2", client_name: "RONALD FLORES PEREZ", client_number: "3253"});
    }};
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    const pending = handlers["CN Complementary Item"].view_related_pending(frm);
    calls[0].resolve({message: {needs_client_selection: true, candidates: [
        {client: "3253", client_name: "RONALD FLORES PEREZ", client_number: "3253", similarity: 92}],
        client_completion: {available: true, modified: "V1"}}}); await tick();
    const dialog = dialogs[0], complete = dialog.fields.find(field => field.fieldname === "complete_client_fields");
    assert.equal(complete.hidden, false);
    await complete.click(); assert.equal(calls.length, 1, "Require an explicit client selection before writing");
    choice = "3253";
    const filling = complete.click(); await complete.click();
    assert.equal(calls.length, 2, "Double click must not submit twice");
    assert.ok(calls[1].args.method.endsWith("complete_client_fields"));
    assert.equal(calls[1].args.type, "POST");
    assert.deepEqual(JSON.parse(JSON.stringify(calls[1].args.args)), {item_name: "ITEM", selected_client: "3253", modified: "V1"});
    calls[1].resolve({message: {updated_fields: {client_name: "RONALD FLORES PEREZ", client_number: "3253"}}}); await filling;
    assert.equal(reloads, 1);
    assert.equal(complete.hidden, 1);
    assert.equal(frm.doc.client_number, "3253");
    const viewing = dialog.primary_action({client: "3253"});
    calls[2].resolve({message: {client: "3253", context: {complementary_item: "ITEM", client_choice: "3253"}}});
    await viewing; await pending;
    assert.equal(shown.length, 1, "Completing fields must not make the current consultation stale");

    // An exact match skips the suggestion picker but still offers completion.
    const exact = handlers["CN Complementary Item"].view_related_pending(frm);
    calls[3].resolve({message: {client: "3253", client_completion: {available: true, client: "3253", modified: "V2"}}}); await exact;
    assert.equal(typeof shown[1].on_complete_client, "function");
    const saving = shown[1].on_complete_client();
    assert.equal(calls[4].args.args.modified, "V2"); calls[4].resolve({message: {}}); await saving;
    assert.equal(reloads, 2);
    console.log("OK: explicit blank-field completion, guarded POST, reload, duplicate prevention and exact/suggested client flows.");
})().catch(error => {console.error(error); process.exitCode = 1;});
