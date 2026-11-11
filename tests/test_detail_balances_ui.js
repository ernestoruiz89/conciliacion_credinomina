const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const handlers = {};
const context = vm.createContext({frappe: {ui: {form: {on: (dt, events) => {handlers[dt] = events;}}}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const row = {name: "R", amount_usd: 165.16, matched_targets: "[]", match_status: "Revisar"};
const target = {detail_row: "R", amount_usd: 147.95};
let refreshed = 0;
const frm = {doc: {detail_rows: [row], targets: [target]}, refresh_field: () => refreshed++};
context.updateDetailPendingAmounts(frm);
assert.equal(row.linked_usd, 147.95);
assert.equal(row.pending_usd, 17.21);
assert.equal(row.match_status, "Revisar");
row.matched_targets = '[{"amount_usd":147.95,"instruction_id":"T"}]';
context.updateDetailPendingAmounts(frm);
assert.equal(row.linked_usd, 147.95, "Do not count manual JSON twice");
target.amount_usd = 170;
handlers["CN Remittance Target"].amount_usd(frm);
assert.equal(row.pending_usd, -4.84);
target.detail_row = "OTHER";
handlers["CN Remittance Target"].detail_row(frm);
assert.equal(row.pending_usd, 165.16, "Removed manual link must disappear");
row.matched_targets = '[{"amount_usd":165.16}]';
context.updateDetailPendingAmounts(frm);
assert.equal(row.pending_usd, 0, "Automatic destinations are included");
row.amount_usd = 175.16;
row.client_credit_usd = 10;
context.updateDetailPendingAmounts(frm);
assert.equal(row.linked_usd, 165.16, "Client credit is not an application");
assert.equal(row.pending_usd, 0, "Documented credit explains the excess on initial form refresh");
assert.equal(row.linked_total_usd, 175.16, "The linked total includes the credit exactly once");
const sirio = {name: 'S', amount_usd: 25.22, linked_usd: 0, client_credit_usd: 25.22,
    company_credit_usd: 0, pending_usd: 0, matched_targets: '[]'};
const sirioForm = {doc: {detail_rows: [sirio], targets: []}, refresh_field() {}};
context.updateDetailPendingAmounts(sirioForm);
assert.equal(sirio.linked_usd, 0, "The client credit remains separate from payments");
assert.equal(sirio.linked_total_usd, 25.22);
assert.equal(sirio.pending_usd, 0);
const formats = {};
context.frappe.format = (value, df, options, doc) => df.formatter
    ? df.formatter(value, df, options, doc) : `US$ ${Number(value).toFixed(2)}`;
context.frappe.meta = {get_docfield: () => ({})};
context.setupRemittanceLinkedTotal({fields_dict: {detail_rows: {grid: {
    update_docfield_property: (name, property, formatter) => {formats[name] = formatter;}
}}}});
assert.equal(formats.linked_total_usd(0, {}, {}, sirio), 'US$ 25.22');
const gridField = {fieldname: 'linked_total_usd', formatter: formats.linked_total_usd};
assert.equal(context.frappe.format(25.22, gridField, {}, sirio), 'US$ 25.22', 'Opening a row must use the currency formatter without recursion');
assert.equal(gridField.formatter, formats.linked_total_usd, 'Do not remove the formatter from shared metadata');
sirio.client_credit_usd = 10; sirio.company_credit_usd = 5;
context.updateDetailPendingAmounts(sirioForm);
assert.equal(sirio.linked_total_usd, 15);
assert.equal(sirio.pending_usd, 10.22);
const partial = {name: 'P', amount_usd: 10, client_credit_usd: 1, company_credit_usd: 0,
    matched_targets: '[{"instruction_id":"RECLASS:abc","amount_usd":2},{"instruction_id":"old","amount_usd":7}]'};
const partialForm = {doc: {detail_rows: [partial], targets: [{detail_row: 'P', amount_usd: 3}]}, refresh_field() {}};
context.updateDetailPendingAmounts(partialForm);
assert.equal(partial.linked_usd, 5, 'Keep the core link while editing other manual targets');
assert.equal(partial.linked_total_usd, 6);
assert.equal(partial.pending_usd, 4);
partialForm.doc.targets = [];
context.updateDetailPendingAmounts(partialForm);
assert.equal(partial.linked_usd, 2, 'Removing a manual target cannot remove a core link');
assert.equal(partial.linked_total_usd, 3);
assert.ok(refreshed > 0);
const fifoRow = {name: "F", source_row: 2, amount_usd: 71.51, matched_targets: "[]", match_status: "Conciliada"};
const fifoLedger = JSON.stringify([{tipo: "Aplicacion historica", aplicacion_id: "OLD", fila_detalle: "F",
    importe_usd: 34.59, origen: "Automática FIFO"}]);
const fifoForm = {doc: {detail_rows: [fifoRow], allocation_detail: fifoLedger, targets: [
    {detail_row: "F", historical_application: "NEW1", amount_usd: 34.59},
    {detail_row: "F", historical_application: "NEW2", amount_usd: 34.59},
]}, refresh_field() {}};
context.updateDetailPendingAmounts(fifoForm);
assert.equal(fifoRow.linked_usd, 103.77);
assert.equal(fifoRow.pending_usd, -32.26);
assert.equal(fifoForm.doc.allocation_detail, fifoLedger, "Preview cannot change financial allocations");
context.remittanceCashAmounts = () => ({pending: 0});
context.remittancePresentation = () => ({reconciled: true});
for (const [status, expected] of [["Diferencia documentada", "Revisar: Diferencia documentada"], ["Conciliada", "Conciliada"]]) {
    const difference = {name: "DIFF", row_kind: "Diferencia documentada", match_status: status,
        amount_usd: -.01, linked_usd: 0, pending_usd: -.01, difference_core_pending_usd: .01,
        matched_targets: "[]", linked_total_usd: 0, match_reason: "Pendiente de respaldo del core"};
    const before = {...difference};
    const form = {doc: {docstatus: 1, detail_rows: [difference], targets: []}, refresh_field() {}};
    context.updateDetailPendingAmounts(form);
    context.updateDetailPendingAmounts(form);
    assert.deepEqual(difference, {...before, match_status: expected});
    assert.equal(context.remittanceIssues(form.doc).length, status === "Conciliada" ? 0 : 1);
}
assert.ok(context.remittanceIssues({...fifoForm.doc, docstatus: 1}).some(issue => issue.text.includes(context.remittanceMoney(32.26))),
    "Show overlinked rows even when their last reconciliation status was Conciliada");
fifoForm.doc.targets = [{detail_row: "F", historical_application: "NEW1", amount_usd: 34.59, result: "Aplicada"}];
context.updateDetailPendingAmounts(fifoForm);
assert.equal(fifoRow.linked_usd, 69.18);
assert.equal(fifoRow.pending_usd, 2.33, "Confirmed FIFO plus a manual application leaves the real remaining balance");
fifoForm.doc.targets = [{detail_row: "F", historical_application: "OLD", amount_usd: 34.59}];
context.updateDetailPendingAmounts(fifoForm);
assert.equal(fifoRow.linked_usd, 34.59, "The same FIFO claim is counted once");
fifoForm.doc.targets = [];
context.updateDetailPendingAmounts(fifoForm);
assert.equal(fifoRow.pending_usd, 36.92, "Removing new targets preserves confirmed cash");
fifoForm.doc.allocation_detail = "";
fifoRow.matched_targets = JSON.stringify([{claim_id: "H:OLD", amount_usd: 34.59,
    instruction_id: "RETAINED:D:F:H:OLD", fifo_applications: [{application_id: "OLD", amount_usd: 34.59}]}]);
context.updateDetailPendingAmounts(fifoForm);
assert.equal(fifoRow.pending_usd, 36.92, "Retained metadata is automatic cash, not a removed manual target");
console.log("OK: partial manual coverage, no double count, removed links, signed pending and automatic destinations.");
