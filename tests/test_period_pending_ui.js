const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let rpc, calls = [], created = [], messages = [];
const context = vm.createContext({
    __: (text, args = []) => String(text).replace(/\{(\d+)\}/g, (_, i) => args[i]),
    format_currency: n => `USD ${Number(n).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        datetime: {get_today: () => "2026-10-08"},
        new_doc: (doctype, values) => created.push({doctype, values}), msgprint: value => messages.push(value),
        call: args => {calls.push(args); return rpc(args);}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
const wrapper = {content: "", handlers: {}, html(value) {this.content = value;}, empty() {this.content = "";},
    off() {this.handlers = {};}, on(event, selector, handler) {this.handlers[selector] = handler;},
    find(selector) {return {val: () => selector.includes("search") ? "Ana" : "Aplicación"};}};
const frm = {doc: {name: "P", employer: "EMP", status: "Cerrado"}, fields_dict: {pending_html: {$wrapper: wrapper}}, is_new: () => false, is_dirty: () => false};
const row = {kind: "Aplicación", doctype: "CN Accounting Import", source: "I", row: 9, client_name: "Ana <script>",
    source_row: "APPLICATION-9", client_number: "5552", loan_number: "001500-1", installment_number: "4",
    applied: 137.33, paid: 111.32, pending: 26.01, status: "Depósito parcial", reason: "<b>motivo</b>"};
const action = name => wrapper.handlers[`[data-pending="${name}"]`];
(async () => {
    rpc = async () => ({message: {rows: Array(50).fill(row), total: 70, count: 70, restricted: []}});
    await context.refreshPeriodPending(frm);
    assert.equal(calls[0].args.period_name, "P");
    assert.ok(wrapper.content.includes("USD 26.01"));
    assert.ok(wrapper.content.includes("&lt;script&gt;") && !wrapper.content.includes("<script>"));
    assert.ok(wrapper.content.includes("/app/cn-accounting-import/I"));
    assert.ok(wrapper.content.includes("Mostrando 1–50 de 70"));
    assert.ok(!wrapper.content.includes('data-pending="complementary"'));
    await action("next")();
    assert.equal(calls.at(-1).args.start, 50);
    await action("filter")();
    assert.equal(calls.at(-1).args.start, 0);
    assert.equal(calls.at(-1).args.search, "Ana");
    assert.equal(calls.at(-1).args.kind, "Aplicación");
    frm.doc.status = "Parcial";
    const second = {...row, source_row: "APPLICATION-80", client_name: "Álvaro", pending: 0.01};
    rpc = async () => ({message: {rows: [row, second], total: 2, count: 2, can_create_complementary: true}});
    await context.refreshPeriodPending(frm);
    assert.ok(wrapper.content.includes('data-pending="complementary"'));
    const click = () => action("complementary")({currentTarget: {dataset: {rowIndex: "1"}}});
    await click();
    assert.equal(created[0].doctype, "CN Complementary Item");
    assert.deepEqual(JSON.parse(JSON.stringify(created[0].values)), {
        employer: "EMP", period: "P", client_name: "Álvaro", client_number: "5552", loan_number: "001500-1",
        installment_number: "4", currency: "USD", posting_date: "2026-10-08", category: "Ajuste de aplicación",
        related_import: "I", related_application: "APPLICATION-80", review_action: "Ajuste de aplicación",
        application_adjustment_usd: 0.01, amount: 0.01,
    });
    frm.is_dirty = () => true;
    await click();
    assert.equal(created.length, 1);
    assert.ok(messages.at(-1).includes("Guarde los cambios"));
    frm.is_dirty = () => false;
    frm.doc.status = "Cerrado";
    await click();
    assert.equal(created.length, 1);
    frm.doc.status = "Parcial";
    context.createPeriodPendingComplementary(frm, {...row, kind: "Cobranza", pending: 10});
    assert.equal(created.at(-1).values.amount, -10);
    assert.equal(created.at(-1).values.related_application, undefined);
    assert.equal(created.at(-1).values.loan_number, "001500-1");
    context.createPeriodPendingComplementary(frm, {kind: "Depósito", pending: 3});
    assert.equal(created.at(-1).values.amount, 3);
    assert.equal(created.at(-1).values.client_number, "");
    const unapplied = {kind: "Cobranza", source: "P", row: 4, applied: 0, paid: 28.43, pending: 28.43,
        status: "Pago pendiente de aplicar", paid_identified: 28.43, pending_application: 28.43,
        pending_deposit: 0, pending_label: "Por aplicar", can_create_complementary: false,
        deposit_evidence: [{name: "DEP <script>", amount_usd: 28.43}]};
    rpc = async () => ({message: {rows: [unapplied], total: 1, count: 1, can_create_complementary: true}});
    await context.refreshPeriodPending(frm);
    assert.ok(wrapper.content.includes("USD 28.43"));
    assert.ok(wrapper.content.includes("Pago pendiente de aplicar"));
    assert.ok(wrapper.content.includes("Por aplicar"));
    assert.ok(wrapper.content.includes("/app/cn-remittance-allocation/DEP%20%3Cscript%3E"));
    assert.ok(wrapper.content.includes("DEP &lt;script&gt;"));
    assert.ok(!wrapper.content.includes('data-pending="complementary"'));
    const before = created.length;
    context.createPeriodPendingComplementary(frm, unapplied);
    assert.equal(created.length, before);
    assert.ok(messages.at(-1).includes("pendiente de aplicar en el core"));
    rpc = async () => ({message: {rows: [{...unapplied, pending_deposit: 10}], total: 1, count: 1}});
    await context.refreshPeriodPending(frm);
    assert.ok(wrapper.content.includes("USD 10.00 por cubrir con depósito"));
    rpc = async () => ({message: {rows: [], count: 0, total: 0, restricted: ["CN Accounting Import"]}});
    await context.refreshPeriodPending(frm);
    assert.ok(wrapper.content.includes("Vista parcial"));
    rpc = async () => {throw Error("fail");};
    await context.refreshPeriodPending(frm);
    assert.ok(action("retry"));
    let finish;
    rpc = () => new Promise(resolve => {finish = resolve;});
    const request = context.refreshPeriodPending(frm);
    frm.doc.name = "P2";
    finish({message: {rows: [row], count: 1, total: 1}});
    await request;
    assert.ok(!wrapper.content.includes("USD 26.01"));
    const count = calls.length;
    frm.is_new = () => true;
    await context.refreshPeriodPending(frm);
    assert.equal(calls.length, count);
    assert.equal(wrapper.content, "");
    console.log("OK: period detected issues, filtering, pagination, escaping, stale requests and closed/new periods.");
})().catch(error => {console.error(error); process.exitCode = 1;});
