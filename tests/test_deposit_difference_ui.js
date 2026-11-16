const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let dialog, requests = [], finish, fail = false, reloads = 0, previewAmount = -.03, hasDifference = true;
const context = vm.createContext({__: text => text, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {dialog = this; this.options = options;}
        show() {this.shown = true;} hide() {this.hidden = true;}
        disable_primary_action() {this.disabled = true;} enable_primary_action() {this.disabled = false;}
    }}, utils: {escape_html: value => String(value)}, show_alert() {}, msgprint() {},
    async call(request) {
        requests.push(request);
        if (request.method.endsWith("preview")) return {message: {modified: "STAMP", employer: "IMMSA", amount_usd: previewAmount,
            difference_reason: "Inmaterial", difference_notes: "Soporte", has_difference: hasDifference, core_item: "CORE"}};
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
    const comments = dialog.options.fields.find(field => field.fieldname === "difference_notes");
    assert.equal(comments.label, 'Comentarios de la diferencia');
    assert.ok(!comments.reqd);
    assert.equal(context.remittanceDetailDifference(frm.doc).difference, -.03, "Company row does not change source total");
    assert.equal(context.canSelectDetailPendingTargets(frm, frm.doc.detail_rows[1]), false);
    const values = {difference_reason: "Inmaterial", core_item: "CORE", amount_usd: -999};
    const pending = dialog.options.primary_action(values);
    await dialog.options.primary_action(values);
    assert.equal(requests.length, 2);
    assert.deepEqual(JSON.parse(JSON.stringify(requests[1].args)), {name: "DEP", modified: "STAMP",
        difference_reason: "Inmaterial", difference_notes: "", core_item: "CORE"});
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

    // A reimport can make the real client detail balance while preserving a
    // previously documented company row. It must not claim a new shortage.
    const balanced = {name: "DEP-9-2026-0232", docstatus: 1, amount_usd: 805.09, employer: "EMP",
        detail_rows: [{name: "CLIENT", deducted_usd: 805.09},
            {name: "OLD", row_kind: "Diferencia documentada", difference_amount_usd: 120.12,
                amount_usd: 120.12, difference_core_pending_usd: 120.12}]};
    const before = JSON.stringify(balanced);
    const banner = context.remittanceDetailDifferenceHtml(balanced, true, true);
    assert.ok(banner.includes("El depósito y el detalle actual coinciden"));
    assert.ok(banner.includes("US$ 0.00") && banner.includes("120.12"));
    assert.ok(!banner.includes("El importe del depósito es diferente al detalle"));
    assert.ok(!banner.includes("−US$ 0.00") && !banner.includes("Pendiente de respaldo del core"));
    assert.ok(!banner.includes("cn-reconcile-detail-difference"));
    assert.ok(banner.includes("Revisar diferencia anterior"));
    assert.equal(context.remittanceDetailDifferenceHtml({...balanced, detail_rows: balanced.detail_rows.slice(0, 1)}), "");
    previewAmount = 0;
    const balancedForm = {...frm, doc: balanced};
    await context.createRemittanceComplementary(balancedForm, {fromDetailDifference: true});
    assert.equal(dialog.options.primary_action_label, "Retirar diferencia anterior");
    assert.ok(dialog.options.fields.find(field => field.fieldname === "difference_reason").read_only);
    assert.equal(dialog.options.fields.find(field => field.fieldname === "difference_reason").reqd, 0);
    assert.ok(dialog.options.fields.find(field => field.fieldname === "information").options.includes("coinciden"));
    const recordsBeforeRemoval = requests.filter(request => request.method.endsWith("record")).length;
    await dialog.options.secondary_action(); // Close leaves documentation intact.
    assert.equal(requests.filter(request => request.method.endsWith("record")).length, recordsBeforeRemoval);
    const removal = dialog.options.primary_action({});
    await dialog.options.primary_action({});
    assert.equal(requests.filter(request => request.method.endsWith("record")).length, recordsBeforeRemoval + 1);
    assert.deepEqual(JSON.parse(JSON.stringify(requests.at(-1).args)), {name: balanced.name, modified: "STAMP", remove: 1});
    finish({message: {}}); await removal;
    assert.equal(reloads, 3);
    assert.equal(JSON.stringify(balanced), before, "Rendering and review never change financial fields");
    hasDifference = false;
    const previousDialog = dialog;
    await context.createRemittanceComplementary(balancedForm, {fromDetailDifference: true});
    assert.equal(dialog, previousDialog, "A balanced deposit without previous documentation needs no edit dialog");
    console.log("OK: signed differences, stale zero difference review, explicit audited removal, concurrency, retry and unchanged financial fields.");
})().catch(error => {console.error(error); process.exitCode = 1;});
