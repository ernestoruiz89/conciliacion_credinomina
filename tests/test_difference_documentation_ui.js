const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const calls = [], handlers = {};
let dialog, finish, saved = 0, rejectSave = false;
const context = vm.createContext({__: text => text, credinomina: {}, frappe: {
    provide() {}, ui: {form: {on: (name, events) => {handlers[name] = events;}}, Dialog: class {
        constructor(options) {this.options = options; dialog = this;}
        show() {this.shown = true;} hide() {this.hidden = true;}
        disable_primary_action() {this.disabled = true;} enable_primary_action() {this.disabled = false;}
    }}, utils: {escape_html: text => text}, show_alert() {},
    async call(request) {
        calls.push(request);
        if (request.method.endsWith("get_documentation")) return {message: {modified: "STAMP", difference_reason: "Otro por aclarar", difference_notes: "Anterior"}};
        if (rejectSave) throw Error("network");
        return new Promise(resolve => {finish = resolve;});
    },
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/public/js/difference_documentation.js", "utf8"), context);
(async () => {
    await context.credinomina.document_difference({doctype: "CN Accounting Import", name: "I", parentfield: "rows", row_name: "R", on_saved: () => {saved++;}});
    assert.ok(dialog.shown);
    assert.equal(dialog.options.fields.find(field => field.fieldname === "difference_notes").default, "Anterior");
    assert.equal(dialog.options.fields.find(field => field.fieldname === "difference_notes").reqd, 1);
    assert.equal(dialog.options.fields.some(field => field.fieldname === "amount"), false);
    const values = {difference_reason: "Diferencia de importe", difference_notes: "Soporte revisado", amount: 0, name: "OTHER", doctype: "CN Complementary Item"};
    const pending = dialog.options.primary_action(values);
    await dialog.options.primary_action(values);
    assert.equal(calls.length, 2, "Repeated confirm cannot send another write");
    assert.deepEqual(JSON.parse(JSON.stringify(calls[1].args)), {
        doctype: "CN Accounting Import", name: "I", parentfield: "rows", row_name: "R", modified: "STAMP",
        difference_reason: "Diferencia de importe", difference_notes: "Soporte revisado",
    });
    finish({message: {}}); await pending;
    assert.equal(saved, 1); assert.equal(dialog.disabled, false); assert.equal(dialog.hidden, true);
    await context.credinomina.document_difference({doctype: "CN Remittance Allocation", name: "D", parentfield: "detail_rows", row_name: "DETAIL"});
    const comments = dialog.options.fields.find(field => field.fieldname === "difference_notes");
    assert.equal(comments.label, 'Comentarios de la diferencia');
    assert.equal(comments.reqd, 0);
    const withoutComments = dialog.options.primary_action({difference_reason: 'Diferencia de importe'});
    assert.equal(calls.at(-1).args.difference_notes, '');
    assert.equal(calls.at(-1).args.row_name, 'DETAIL');
    finish({message: {}}); await withoutComments;
    await context.credinomina.document_difference({doctype: "CN Remittance Allocation", name: "D"});
    rejectSave = true;
    await assert.rejects(dialog.options.primary_action(values), /network/);
    assert.equal(dialog.disabled, false); assert.ok(!dialog.hidden, "Failed documentation stays open for retry");
    assert.equal(saved, 1);
    assert.ok(handlers["CN Source Row"].document_difference);
    assert.ok(handlers["CN Collection Row"].document_difference);
    console.log("OK: document-only scope, strict payload, repeated clicks, retry, and native row actions.");
})().catch(error => {console.error(error); process.exitCode = 1;});
