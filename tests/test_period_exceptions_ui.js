const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let events, rpc, calls = [], route;
const esc = value => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const context = vm.createContext({
    __: (text, args = []) => String(text).replace(/\{(\d+)\}/g, (_, i) => args[i]),
    format_currency: n => `USD ${Number(n).toFixed(2)}`,
    frappe: {ui: {form: {on: (_, handlers) => {events = handlers;}}},
        utils: {escape_html: esc}, datetime: {str_to_user: date => `LOCAL:${date}`},
        call: args => {calls.push(args); return rpc(args);}, set_route: (...args) => {route = args;},
        session: {user: "operator"}, user: {has_role: () => false}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
function form() {
    const wrapper = {content: "", handlers: {}, html(value) {this.content = value;},
        empty() {this.content = "";}, off() {this.handlers = {};},
        on(event, selector, handler) {this.handlers[selector] = handler;}};
    return {doc: {name: "P1", status: "Cerrado", reconciliation_mode: "Historica"},
        fields_dict: {exceptions_html: {$wrapper: wrapper}}, fields: [],
        is_new: () => false, set_df_property() {}, disable_save() {}, set_intro() {}, add_custom_button() {}};
}
const row = i => ({name: `CN-EXC-${i}`, exception_type: '<img src=x onerror="bad()">',
    client_name: "Ana <script>", client_number: "123", loan_number: "456-1",
    status: "En revision", amount_usd: 12.5, assigned_to: "user@example.com", commitment_date: "2026-10-05"});
const action = (frm, name) => frm.fields_dict.exceptions_html.$wrapper.handlers[`[data-action="${name}"]`];
(async () => {
    let frm = form();
    rpc = async () => ({message: Array.from({length: 21}, (_, i) => row(i))});
    await context.refreshPeriodExceptions(frm);
    const args = calls.at(-1).args;
    assert.equal(calls.at(-1).method, "frappe.client.get_list");
    assert.equal(args.filters.period, "P1");
    assert.deepEqual(Array.from(args.filters.status[1]), ["Abierta", "En revision"]);
    assert.equal(args.limit_page_length, 21);
    let html = frm.fields_dict.exceptions_html.$wrapper.content;
    assert.ok(html.includes("&lt;img") && !html.includes("<img"));
    assert.ok(html.includes("LOCAL:2026-10-05"));
    assert.ok(html.includes("USD 12.50") && html.includes("En revisión"));
    assert.ok(html.includes("/app/cn-reconciliation-exception/CN-EXC-0"));
    assert.ok(!html.includes("CN-EXC-20"));
    await action(frm, "next")();
    assert.equal(calls.at(-1).args.limit_start, 20);
    await action(frm, "previous")();
    assert.equal(calls.at(-1).args.limit_start, 0);
    await action(frm, "closed")({currentTarget: {checked: true}});
    assert.equal(calls.at(-1).args.filters.status, undefined);
    action(frm, "list")();
    assert.equal(route[1], "CN Reconciliation Exception");
    assert.equal(route[2].period, "P1");
    rpc = async () => ({message: []});
    await context.refreshPeriodExceptions(frm);
    assert.ok(frm.fields_dict.exceptions_html.$wrapper.content.includes("No hay excepciones abiertas"));
    await context.refreshPeriodExceptions(frm, 0, true);
    assert.ok(frm.fields_dict.exceptions_html.$wrapper.content.includes("No hay excepciones registradas"));
    rpc = async () => {throw Error("permission denied");};
    await context.refreshPeriodExceptions(frm);
    assert.ok(frm.fields_dict.exceptions_html.$wrapper.content.includes("Compruebe sus permisos"));
    assert.ok(action(frm, "retry"));
    // Old responses cannot overwrite a different period or a newer filter.
    let finish;
    rpc = () => new Promise(resolve => {finish = resolve;});
    const pending = context.refreshPeriodExceptions(frm);
    frm.doc.name = "P2";
    rpc = async () => ({message: []});
    await context.refreshPeriodExceptions(frm);
    finish({message: [row(99)]}); await pending;
    assert.ok(!frm.fields_dict.exceptions_html.$wrapper.content.includes("CN-EXC-99"));
    const count = calls.length;
    frm.is_new = () => true;
    await context.refreshPeriodExceptions(frm);
    assert.equal(calls.length, count);
    assert.equal(frm.fields_dict.exceptions_html.$wrapper.content, "");
    // Closed historical periods still load the panel before refresh returns.
    frm = form();
    events.refresh(frm);
    assert.equal(calls.length, count + 1);
    await Promise.resolve();
    console.log("OK: period exceptions, permissions-aware API, filters, pagination, links, dates, safe HTML and closed periods.");
})().catch(error => {console.error(error); process.exitCode = 1;});
