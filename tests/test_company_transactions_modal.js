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
const payload = (rows, totals = {original_usd: 10500, adjustment_usd: 2100, net_usd: 8400, assigned_usd: 3150, rounding_usd: 0, pending_usd: 5250}) =>
    ({message: {rows, totals, summary: [{label: "Conciliadas", value: 100}], total: 105, filtered_count: 105, start: 0}});

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
    assert.ok(dialog.html.includes('font-size:12px;line-height:1.4'));
    assert.ok(dialog.html.includes('<tfoot>'));
    assert.ok(dialog.html.includes('Total filtrado · 105 transacciones'));
    assert.ok(dialog.html.includes('USD 10500.00')); // Full filtered total, not the single visible row.
    assert.ok(dialog.html.includes('todas las páginas'));
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
        original_currency: "NIO", original_amount: 3662.43, original_usd: 100, assigned_usd: 80, surplus_usd: 20, pending_usd: 0}],
        {original_usd: 10500, assigned_usd: 8400, surplus_usd: 2100, pending_usd: 0}));
    await flush();
    assert.ok(deposits.html.includes("/app/cn-remittance-allocation/DEP-4-2025-0001"));
    assert.ok(deposits.html.includes("Saldo a favor US$"));
    assert.ok(deposits.html.includes("BANPRO"));
    assert.ok(deposits.html.includes("USD 20.00"));
    assert.ok(deposits.html.includes("USD 2100.00"));
    assert.ok(deposits.fields[0].options.includes("Incluye borradores"));
    deposits.primary_action();
    requests.shift().resolve(payload([], {original_usd: 100, assigned_usd: null, surplus_usd: 0, pending_usd: null}));
    await flush();
    const footer = deposits.html.split("<tfoot>")[1].split("</tfoot>")[0];
    assert.equal((footer.match(/>—</g) || []).length, 2);
    for (const kind of ["Partidas complementarias contables", "Partidas complementarias sin origen contable"]) {
        const complementary = report.show_month_detail({year: 2025, month: 4, employer: "A", transaction_type: kind, include_drafts: 0});
        assert.equal(calls.at(-1).args.transaction_type, kind);
        requests.shift().resolve(payload([{document: "COMP-1", date: "2025-04-30", state: "Parcial", category: "Compensación entre partidas",
            client_name: "Ana", client_number: "10", loan_number: "100-1", signed_usd: -100, original_usd: 100,
            resolved_usd: 60, pending_usd: 40, used_label: "Compensado", reason: "<script>unsafe</script>"}],
            {original_usd: 100, resolved_usd: 60, pending_usd: 40}));
        await flush();
        assert.ok(complementary.html.includes("/app/cn-complementary-item/COMP-1"));
        assert.ok(complementary.html.includes("Importe absoluto US$"));
        assert.ok(complementary.html.includes("Resuelto US$"));
        assert.ok(complementary.html.includes("USD -100.00"));
        assert.ok(complementary.html.includes("USD 60.00"));
        assert.ok(complementary.html.includes("Compensado"));
        assert.ok(!complementary.html.includes("<script>"));
        assert.ok(complementary.html.includes("no equivale a efectivo recibido"));
        assert.ok(!complementary.html.includes("/app/cn-remittance-allocation/COMP-1"));
    }
    console.log("OK: month drill-down, filters, paging, dates, document links, escaping, races and errors.");
})().catch(error => { console.error(error); process.exitCode = 1; });
