const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let rpc, calls = [];
const context = vm.createContext({
    __: (text, args = []) => String(text).replace(/\{(\d+)\}/g, (_, i) => args[i]),
    format_currency: n => `USD ${Number(n).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        call: args => {calls.push(args); return rpc(args);}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
const wrapper = {content: "", handlers: {}, html(value) {this.content = value;}, empty() {this.content = "";},
    off() {this.handlers = {};}, on(event, selector, handler) {this.handlers[selector] = handler;},
    find(selector) {return {val: () => selector.includes("search") ? "Ana" : "Aplicación"};}};
const frm = {doc: {name: "P", status: "Cerrado"}, fields_dict: {pending_html: {$wrapper: wrapper}}, is_new: () => false};
const row = {kind: "Aplicación", doctype: "CN Accounting Import", source: "I", row: 9, client_name: "Ana <script>",
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
    await action("next")();
    assert.equal(calls.at(-1).args.start, 50);
    await action("filter")();
    assert.equal(calls.at(-1).args.start, 0);
    assert.equal(calls.at(-1).args.search, "Ana");
    assert.equal(calls.at(-1).args.kind, "Aplicación");
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
