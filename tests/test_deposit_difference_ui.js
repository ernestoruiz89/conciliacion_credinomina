const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let dialog, requests = [], finish, fail = false, reloads = 0;
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {dialog = this; this.options = options;}
        show() {this.shown = true;} hide() {this.hidden = true;}
        disable_primary_action() {this.disabled = true;} enable_primary_action() {this.disabled = false;}
    }}, utils: {escape_html: value => String(value)}, show_alert() {}, msgprint() {},
    async call(request) {
        requests.push(request);
        if (request.method.endsWith("preview")) return {message: {modified: "STAMP", employer: "IMMSA", amount_usd: -.03,
            difference_reason: "Inmaterial", difference_notes: "Soporte", has_difference: true, core_item: "CORE"}};
        if (fail) throw Error("retry");
        return new Promise(resolve => {finish = resolve;});
    },
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js", "utf8"), context);
(async () => {
    const frm = {doc: {name: "DEP", docstatus: 1, amount_usd: 6563.02, employer: "IMMSA",
        detail_rows: [{name: "CLIENT", deducted_usd: 6563.05}, {name: "D", row_kind: "Diferencia documentada", amount_usd: -.03}]},
        is_new: () => false, is_dirty: () => false, reload_doc: async () => {reloads++;}};
    await context.createRemittanceComplementary(frm, {fromDetailDifference: true});
    assert.ok(dialog.shown);
    assert.equal(dialog.options.fields.find(field => field.fieldname === "amount_usd").read_only, 1);
    assert.equal(context.remittanceDetailDifference(frm.doc).difference, -.03, "Company row does not change source total");
    assert.equal(context.canSelectDetailPendingTargets(frm, frm.doc.detail_rows[1]), false);
    const values = {difference_reason: "Inmaterial", difference_notes: "Verificado", core_item: "CORE", amount_usd: -999};
    const pending = dialog.options.primary_action(values);
    await dialog.options.primary_action(values);
    assert.equal(requests.length, 2);
    assert.deepEqual(JSON.parse(JSON.stringify(requests[1].args)), {name: "DEP", modified: "STAMP",
        difference_reason: "Inmaterial", difference_notes: "Verificado", core_item: "CORE"});
    finish({message: {}}); await pending;
    assert.equal(reloads, 1);
    await context.createRemittanceComplementary(frm, {detailRowName: "D"});
    fail = true;
    await assert.rejects(dialog.options.primary_action(values), /retry/);
    assert.ok(!dialog.hidden && !dialog.disabled);
    fail = false;
    const removed = dialog.options.secondary_action();
    assert.equal(requests.at(-1).args.remove, 1);
    finish({message: {}}); await removed;
    assert.equal(reloads, 2);
    console.log("OK: banner and company row open signed-difference modal; strict payload, core link, retry, removal.");
})().catch(error => {console.error(error); process.exitCode = 1;});
