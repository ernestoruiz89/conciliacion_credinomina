const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let rpc, calls = [], created = [], messages = [];
const context = vm.createContext({
    __: (text, args = []) => String(text).replace(/\{(\d+)\}/g, (_, i) => args[i]),
    format_currency: n => `USD ${Number(n).toFixed(2)}`,
    credinomina: {document_difference: async options => created.push(options)},
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
    rpc = async () => ({message: {rows: [row, second], total: 2, count: 2, can_document_difference: true}});
    await context.refreshPeriodPending(frm);
    assert.ok(wrapper.content.includes('data-pending="complementary"'));
    const click = () => action("complementary")({currentTarget: {dataset: {rowIndex: "1"}}});
    await click();
    assert.equal(created[0].doctype, "CN Accounting Import");
    assert.equal(created[0].name, "I");
    assert.equal(created[0].row_name, "APPLICATION-80");
    assert.equal(created[0].parentfield, "rows");
    assert.equal(created[0].amount, undefined);
    frm.is_dirty = () => true;
    await click();
    assert.equal(created.length, 1);
    assert.ok(messages.at(-1).includes("Guarde los cambios"));
    frm.is_dirty = () => false;
    frm.doc.status = "Cerrado";
    await click();
    assert.equal(created.length, 2, "Classification is allowed on closed periods without reopening them");
    const unapplied = {...row, kind: "Cobranza", doctype: "CN Reconciliation Period", source: "P", source_row: "C", pending_application: 10};
    await context.createPeriodPendingComplementary(frm, unapplied);
    assert.equal(created.at(-1).parentfield, "collection_rows");
    assert.equal(created.at(-1).row_name, "C");
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
