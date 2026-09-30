const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
const handlers = {};
let html, dialog, route;
const root = {appendTo() {return this;}, html(value) {html = value; return this;},
    on(event, selector, fn) {handlers[selector] = fn; return this;}};
const deposit = {name: "D1", employer: "E", month: "2025-05", date: "2025-05-20", reference: '<DEP&1>',
    total_usd: 1000, credits_usd: 800, other_usd: 100, credit_balance_usd: 100,
    unclassified_usd: 0, review_usd: 0, original_amount: 36624.3, currency: "NIO",
    result: "Parcial con saldo a favor", settled: false, needs_review: false, shared: true,
    destinations: [
        {type: "Créditos", label: "P-MARZO", month: "2025-03", amount_usd: 300},
        {type: "Créditos", label: "P-ABRIL", month: "2025-04", amount_usd: 500},
        {type: "Partida complementaria", label: "X1 · Cobranza administrativa", month: "2025-04", amount_usd: 100},
    ]};
const data = {year: 2025, periods: [{name: "P-ABRIL", employer: "E", month: "2025-04",
    reconciliation_mode: "Historica", control_state: "historico_conciliado", applied_usd: 500, remitted_usd: 500}],
    cash_deposits: [deposit, {...deposit, name: "D2", employer: "SOLO-DEPOSITOS", month: "2025-06", date: "2025-06-01"}], totals: {}};
const context = vm.createContext({__: text => text, $: value => typeof value === "string" ? root : {attr: name => value[name]},
    frappe: {pages: {"control-credinomina": {}}, datetime: {str_to_user: value => value.split("-").reverse().join("/")},
        set_route: (...args) => {route = args;}, call: async () => ({message: data}), ui: {
            make_app_page: () => ({main: {}, add_field: df => ({df, refresh() {}, set_input() {},
                get_value: () => df.fieldname === "year" ? "2025" : ""}), add_button() {}, set_primary_action() {}}),
            Dialog: class {
                constructor(options) {dialog = this; this.options = options; this.events = {}; this.wrapper = {
                    html: value => {this.html = value;}, on: (event, selector, fn) => {this.events[selector] = fn;}};}
                get_field() {return {$wrapper: this.wrapper};} show() {this.shown = true;} hide() {this.shown = false;}
            },
        }},
});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.helpers = {summarizeCash, renderCashDistribution, renderCashCard, renderMonthSummary}; })();"), context);
const {summarizeCash, renderCashDistribution, renderCashCard, renderMonthSummary} = context.helpers;
assert.equal(summarizeCash([deposit, deposit]).total_usd, 1000);
assert.equal(summarizeCash([deposit, deposit]).count, 1);
assert.equal(summarizeCash([deposit]).creditCount, 1);
assert.equal(summarizeCash([deposit]).settled, 0);
assert.equal(summarizeCash([{...deposit, needs_review: true}]).review, 1);
assert.equal(summarizeCash([{name: "A", total_usd: 0.1}, {name: "B", total_usd: 0.2}]).total_usd, 0.3);
const breakdown = renderCashDistribution(deposit);
assert.ok(breakdown.includes("P-MARZO") && breakdown.includes("P-ABRIL") && breakdown.includes("Cobranza administrativa"));
assert.ok(breakdown.includes("20/05/2025"));
assert.ok(breakdown.includes("&lt;DEP&amp;1&gt;") && !breakdown.includes("<DEP&1>"));
assert.ok(breakdown.includes("NIO") && breakdown.includes("36,624.30"));
assert.ok(breakdown.includes("no significa que ya fue reembolsado"));
assert.ok(renderCashCard(deposit).includes("Distribuido entre varios períodos"));
assert.ok(renderMonthSummary(data.periods, "E", "2025-04", []).includes("Asignado / aplicado"));
assert.ok(!renderMonthSummary(data.periods, "E", "2025-04", []).includes("Depósitos recibidos en el mes"));
assert.ok(renderMonthSummary([], "E", "2025-05", [deposit]).includes("Sin período de cobranza este mes"));
const original = JSON.stringify(data);
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({});
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(html.includes('data-month="2025-04"'));
    assert.ok(html.includes('data-month="2025-05"'));
    assert.ok(html.includes('data-month="2025-06"'));
    assert.ok(html.includes("SOLO-DEPOSITOS"));
    handlers["[data-month]"].call({"data-employer": "E", "data-month": "2025-05"});
    const month = dialog;
    assert.ok(month.shown && month.html.includes('data-cash-deposit="D1"'));
    assert.ok(!month.html.includes('data-cash-deposit="D2"'));
    assert.ok(!month.html.includes('data-month-period="P-ABRIL"'));
    assert.ok(month.html.includes("No hay períodos de cobranza en este mes"));
    month.events["[data-cash-deposit]"].call({"data-cash-deposit": "D1"});
    assert.equal(month.shown, false);
    assert.equal(dialog.options.title, "Distribución del depósito");
    assert.ok(dialog.shown && dialog.html.includes("P-ABRIL"));
    const distribution = dialog;
    distribution.options.secondary_action();
    assert.ok(month.shown && !distribution.shown);
    distribution.options.primary_action();
    assert.deepEqual(route, ["Form", "CN Remittance Allocation", "D1"]);
    assert.equal(JSON.stringify(data), original);
    console.log("OK: receipts by actual month, no-period companies, unique full totals, deposit cards, multi-period distribution, fees, credit balances and navigation.");
})().catch(error => {console.error(error); process.exitCode = 1;});
