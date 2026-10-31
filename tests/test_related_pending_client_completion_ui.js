const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let modal, complete, finish, count = 0;
const context = vm.createContext({window: {}, __: text => text, format_currency: String, frappe: {
    utils: {escape_html: String}, ui: {Dialog: function(options) {
        Object.assign(this, options); this.fields_dict = {};
        for (const field of options.fields) this.fields_dict[field.fieldname] = {df: field,
            $wrapper: {html() {}, hide() {this.hidden = true;}}, $input: {prop(name, value) {this[name] = value;}}};
        this.show = () => {}; this.hide = () => {}; modal = this;
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/public/js/related_pending_modal.js", "utf8"), context);
(async () => {
    context.window.credinominaShowRelatedPending({on_complete_client: () => {
        count++; return new Promise(resolve => {finish = resolve;});
    }});
    complete = modal.fields_dict.complete_client_fields;
    assert.equal(complete.df.label, "Completar datos vacíos de la partida");
    const running = complete.df.click(); await complete.df.click();
    assert.equal(count, 1); assert.equal(complete.$input.disabled, true);
    finish(true); await running;
    assert.equal(complete.$wrapper.hidden, true);
    assert.equal(complete.$input.disabled, false);
    context.window.credinominaShowRelatedPending({});
    assert.equal(modal.fields_dict.complete_client_fields, undefined, "Other clients' modals must remain read-only");
    console.log("OK: optional completion action in the linked-records modal and duplicate protection.");
})().catch(error => {console.error(error); process.exitCode = 1;});
