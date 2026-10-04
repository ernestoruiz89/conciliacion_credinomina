const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const calls = [], requests = [];
const escape = value => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
class Dialog {
    constructor(options) {
        Object.assign(this, options);
        this.values = {};
        this.handlers = {};
        this.fields_dict = {detail: {$wrapper: {
            html: value => { this.html = value; },
            on: (event, selector, callback) => { this.handlers[selector] = callback; },
        }}};
    }
    get_value(key) { return this.values[key]; }
    show() { this.visible = true; }
}
const context = vm.createContext({
    __: value => value,
    format_currency: (value, currency) => `${currency} ${Number(value).toFixed(2)}`,
    frappe: {query_reports: {}, ui: {Dialog}, utils: {escape_html: escape},
        datetime: {get_today: () => "2026-10-04", str_to_user: value => `LOCAL(${value})`},
        call: options => { calls.push(options); return new Promise((resolve, reject) => requests.push({resolve, reject})); },
    },
    $: element => ({attr: name => element[name]}),
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/report/transacciones_por_empresa/transacciones_por_empresa.js"), "utf8"), context);
const report = context.frappe.query_reports["Transacciones por Empresa"];
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = rows => ({message: {rows, summary: [{label: "Conciliadas", value: 100}], total: 105, filtered_count: 105, start: 0}});

(async () => {
    let click, offCalls = 0;
    const wrapper = {off: () => offCalls++, on: (event, selector, handler) => { click = handler; }};
    report.onload({page: {wrapper}});
    report.onload({page: {wrapper}});
    assert.equal(offCalls, 2); // Reopening a report replaces its listener.
    const originalOpen = report.show_month_detail;
    let clicked;
    report.show_month_detail = args => { clicked = args; };
    click.call({"data-detail": JSON.stringify({year: 2025, month: 4, employer: "A", include_drafts: 1})}, {preventDefault() {}});
    assert.equal(clicked.year, 2025);
    assert.equal(clicked.include_drafts, 1);
    report.show_month_detail = originalOpen;

    const dialog = report.show_month_detail({year: 2025, month: 4, employer: 'A<script>', transaction_type: "Aplicaciones", include_drafts: 0});
    assert.ok(dialog.visible);
    assert.ok(dialog.html.includes("Cargando"));
    assert.ok(!dialog.fields[0].options.includes("<script>"));
    assert.equal(calls[0].args.month, 4);
    const row = {doctype: "CN Accounting Import", document: 'IMPORT A"', row_index: 3, date: "2025-04-30", state: "Parcial",
        client_name: "<script>alert(1)</script>", client_number: "1", loan_number: "100-1", original_usd: 100, adjustment_usd: 20,
        net_usd: 80, assigned_usd: 30, rounding_usd: 0, pending_usd: 50, observations: "<b>Shared</b>"};
    requests.shift().resolve(payload([row]));
    await flush();
    assert.ok(dialog.html.includes("LOCAL(2025-04-30)"));
    assert.ok(dialog.html.includes("USD 50.00"));
    assert.ok(!dialog.html.includes("<script>"));
    assert.ok(!dialog.html.includes("<b>Shared</b>"));
    assert.ok(dialog.html.includes("/app/cn-accounting-import/IMPORT%20A%22"));
    assert.ok(dialog.html.includes("Fila 3"));
    dialog.handlers[".cn-detail-next"]();
    assert.equal(calls.at(-1).args.start, 100);
    dialog.handlers[".cn-detail-next"]();
    assert.equal(requests.length, 1); // Ignore double-click while loading.
    const stale = requests.shift();
    dialog.values = {state: "Pendiente", search: "Juan"};
    dialog.primary_action();
    assert.equal(calls.at(-1).args.start, 0);
    assert.equal(calls.at(-1).args.search, "Juan");
    assert.equal(calls.at(-1).args.state, "Pendiente");
    requests.shift().resolve(payload([]));
    await flush();
    const latest = dialog.html;
    stale.resolve(payload([row]));
    await flush();
    assert.equal(dialog.html, latest); // An older request cannot replace new filters.
    assert.ok(dialog.html.includes("No hay transacciones"));
    dialog.primary_action();
    requests.shift().reject(new Error("Unavailable"));
    await flush();
    assert.ok(dialog.html.includes("reintentar"));
    dialog.primary_action();
    dialog.onhide();
    const before = dialog.html;
    requests.shift().resolve(payload([row]));
    await flush();
    assert.equal(dialog.html, before);

    const deposits = report.show_month_detail({year: 2025, month: 4, employer: "A", transaction_type: "Depósitos", include_drafts: 1});
    requests.shift().resolve(payload([{document: "DEP-4-2025-0001", state: "Conciliado", bank_account: "BANPRO",
        original_currency: "NIO", original_amount: 3662.43, original_usd: 100, assigned_usd: 80, surplus_usd: 20, pending_usd: 0}]));
    await flush();
    assert.ok(deposits.html.includes("/app/cn-remittance-allocation/DEP-4-2025-0001"));
    assert.ok(deposits.html.includes("Saldo a favor US$"));
    assert.ok(deposits.html.includes("BANPRO"));
    assert.ok(deposits.html.includes("USD 20.00"));
    assert.ok(deposits.fields[0].options.includes("Incluye borradores"));
    console.log("OK: month drill-down, filters, paging, dates, document links, escaping, races and errors.");
})().catch(error => { console.error(error); process.exitCode = 1; });
