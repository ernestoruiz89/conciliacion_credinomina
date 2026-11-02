const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let handlers;
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on: (_, events) => {handlers = events;}}},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_credit_portfolio_snapshot/cn_credit_portfolio_snapshot.js", "utf8"), context);

let label, uploads = 0, deletions = 0;
const control = {
    $value: {find(selector) {
        assert.equal(selector, '[data-action="clear_attachment"]');
        return {text(value) {label = value;}};
    }},
    clear_attachment() {assert.equal(this, control); deletions++;},
    on_attach_click() {assert.equal(this, control); uploads++;},
};
const frm = {doc: {name: "CUT", source_file: "/private/files/original.xlsx", file_hash: "IMPORTED"},
    fields_dict: {source_file: control}, is_new: () => false, is_dirty: () => false,
    add_custom_button() {},
};

handlers.refresh(frm);
assert.equal(label, "Reemplazar");
const replacementAction = control.clear_attachment;
control.clear_attachment();
assert.equal(uploads, 1);
assert.equal(deletions, 0, "Replacing an imported file must never invoke the deletion action");
assert.equal(frm.doc.source_file, "/private/files/original.xlsx", "Opening or cancelling the uploader keeps the original source");
assert.equal(frm.doc.file_hash, "IMPORTED");

// The native upload callback updates the source URL. A refresh must not wrap
// the control twice or start the import before the user requests it.
frm.doc.source_file = "/private/files/corrected.xlsx";
handlers.refresh(frm);
assert.equal(control.clear_attachment, replacementAction);
control.clear_attachment();
assert.equal(uploads, 2);
assert.equal(deletions, 0);
assert.equal(frm.doc.source_file, "/private/files/corrected.xlsx");

// Reusing the form for an unimported draft restores ordinary Clear behavior.
frm.doc = {name: "DRAFT", source_file: "/private/files/draft.xlsx", file_hash: ""};
handlers.refresh(frm);
assert.equal(label, "Clear");
control.clear_attachment();
assert.equal(deletions, 1);
assert.equal(uploads, 2);

console.log("OK: imported portfolio files are replaced without deletion; cancelling, refreshes and draft behavior are preserved.");
