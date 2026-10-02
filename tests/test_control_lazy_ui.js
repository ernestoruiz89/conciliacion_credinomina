const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {}, dialogs = [], requests = [];
let refresh, html, year = "2026", responseData;
const root = {appendTo() {return this;}, html(value) {html = value; return this;},
    on(event, selector, callback) {handlers[selector] = callback; return this;},
    find() {return {prop() {}};}};
const data = () => ({year: 2026, totals: {}, work_items: [],
    periods: [{name: "P", employer: "A", month: "2026-09", reconciliation_mode: "Historica",
        applied_usd: 100, remitted_usd: 100, control_state: "historico_conciliado", detail_loaded: false}],
    cash_deposits: [{name: "D", employer: "A", month: "2026-09", total_usd: 100, credits_usd: 100,
        result: "Conciliado", detail_loaded: false}],
});
const context = vm.createContext({
    __: value => value,
    $: value => typeof value === "string" ? root : {attr: key => value[key]},
    frappe: {pages: {"control-credinomina": {}}, datetime: {str_to_user: value => value},
        call(args) {
            if (args.method.endsWith("get_control_data")) return Promise.resolve({message: responseData || data()});
            return new Promise((resolve, reject) => requests.push({args, resolve, reject}));
        },
        ui: {
            make_app_page: () => ({main: {}, add_field: df => ({df, refresh() {}, set_input() {}, get_value: () => df.fieldname === "year" ? year : ""}),
                add_button() {}, set_primary_action(label, fn) {refresh = fn;}}),
            Dialog: class {
                constructor(options) {this.options = options; this.events = {}; dialogs.push(this);
                    this.wrapper = {html: value => {this.html = value;}, on: (e, selector, fn) => {this.events[selector] = fn;}};}
                get_field() {return {$wrapper: this.wrapper};} show() {this.shown = true;} hide() {this.shown = false;}
            },
        },
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8"), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({}); await tick();
    assert.equal(requests.length, 0, "Initial load should not request details");
    handlers["[data-period]"].call({"data-period": "P"});
    handlers["[data-period]"].call({"data-period": "P"});
    assert.equal(requests.length, 1, "Double click must share the pending request");
    assert.equal(requests[0].args.args.period_name, "P");
    requests[0].resolve({message: {name: "P", detail_loaded: true, historical_rows: [
        {name: "R", client_name: "Ana <Test>", amount: 100, historical_balance_usd: 0},
    ]}}); await tick();
    assert.equal(dialogs.length, 1);
    assert.match(dialogs[0].html, /Ana &lt;Test&gt;/);
    handlers["[data-period]"].call({"data-period": "P"}); await tick();
    assert.equal(requests.length, 1, "Reopening uses page-local detail cache");
    await refresh();
    handlers["[data-period]"].call({"data-period": "P"});
    await refresh();
    const before = dialogs.length;
    requests[1].resolve({message: {name: "P", detail_loaded: true, historical_rows: []}}); await tick();
    assert.equal(dialogs.length, before, "A refresh must invalidate pending detail responses");
    handlers["[data-month]"].call({"data-employer": "A", "data-month": "2026-09"});
    const month = dialogs.at(-1);
    const openDeposit = () => month.events["[data-cash-deposit]"].call({"data-cash-deposit": "D"});
    const first = openDeposit(); requests[2].reject(new Error("Network")); await first;
    assert.equal(month.shown, true, "Keep month visible after a failed request");
    const second = openDeposit();
    assert.equal(requests[3].args.args.deposit_name, "D");
    requests[3].resolve({message: {name: "D", total_usd: 100, result: "Conciliado", detail_loaded: true, destinations: [
        {type: "Créditos", label: "P", amount_usd: 100, people: [{client_name: "Ana", amount_usd: 100}]},
    ]}}); await second;
    assert.equal(month.shown, false);
    assert.match(dialogs.at(-1).html, /Distribución por persona/);
    dialogs.at(-1).options.secondary_action(); assert.equal(month.shown, true);
    const work = i => ({priority: 1, kind: "review", employer_name: "A", summary: `Gestión ${i}`, period_label: "2026-09"});
    responseData = {...data(), work_items: Array.from({length: 100}, (_, i) => work(i)), work_item_count: 150};
    await refresh();
    assert.match(html, /100 de 150/);
    const page = handlers["[data-more-work]"].call({});
    const more = requests.at(-1);
    assert.equal(more.args.args.start, 100);
    assert.equal(more.args.args.section, "work_items");
    more.resolve({message: {rows: Array.from({length: 50}, (_, i) => work(100 + i)), count: 150}});
    await page;
    assert.match(html, /Gestión 149/);
    assert.doesNotMatch(html, /data-more-work/);
    console.log("OK: lazy details, cache, deduplication, stale responses, retry, month navigation and pagination.");
})().catch(error => {console.error(error); process.exitCode = 1;});
