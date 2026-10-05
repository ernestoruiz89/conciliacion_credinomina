const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {}, requests = [], controls = {};
let html, slot, refresh, route;
const root = {appendTo() {return this;}, html(value) {html = value; return this;},
    on(event, selector, fn) {handlers[`${event}:${selector}`] = fn; return this;},
    find() {return {html(value) {slot = value;}};}};
const payload = {as_of_date: "2026-10-02",
    applications: {net_applied_usd: 1000, pending_usd: 100, overdue_usd: 80, unlinked_usd: 20,
        without_date_usd: 10, missing_fx_count: 1},
    deposits: {received_usd: 1200, deposit_count: 2, unassigned_usd: 150, unassigned_count: 1, overallocated_count: 1},
    credits: {credit_pending_usd: 150, client_pending_usd: 100, company_documented_usd: 50},
    receivables: {pending_usd: 10, overdue_usd: 3, without_date_usd: 7},
    exceptions: {count: 3, filters: {status: ["in", ["Abierta", "En revision"]], commitment_date: ["<=", "2026-10-01"]}},
};
const context = vm.createContext({__: value => value, $: value => typeof value === "string" ? root : {attr: key => value[key]},
    frappe: {pages: {"control-credinomina": {}}, set_route: (...args) => {route = args;},
        call(args) {
            if (args.method.endsWith("get_control_data")) return Promise.resolve({message: {
                year: controls.year.value, totals: {}, periods: [], work_items: [], available_years: [2025, 2026],
            }});
            const promise = new Promise((resolve, reject) => requests.push({args, resolve, reject}));
            return {then: promise.then.bind(promise)}; // Frappe's jqXHR is a thenable, not a native Promise.
        }, ui: {make_app_page: () => ({main: {}, add_button() {}, set_primary_action(label, fn) {refresh = fn;},
            add_field(df) {return controls[df.fieldname] = {df, value: df.default || "", refresh() {},
                set_input(value) {this.value = value;}, get_value() {return this.value;}};},
        })},
    },
});
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.renderKpis = renderControlKpis; })();"), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
const click = selector => handlers[`click:${selector}`].call({});
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({}); await tick();
    assert.equal(requests.length, 0, "Calendar load must not query KPI details");
    assert.match(html, /Detalle del proceso/);
    assert.doesNotMatch(html, /Histórico sin depósito|Movimientos de conciliación/);
    const first = click("[data-kpis-toggle]");
    click("[data-kpis-toggle]"); // Close while fetching.
    click("[data-kpis-toggle]"); // Reopen; reuse request.
    assert.equal(requests.length, 1);
    requests[0].resolve({message: payload}); await first;
    assert.equal((slot.match(/class="cn-kpi-label"/g) || []).length, 7);
    assert.match(slot, /CxC por ajustes US\$/);
    assert.match(slot, /Sin fecha compromiso/);
    assert.match(slot, /USD\s*1,000\.00/);
    assert.match(slot, /aplicado y pendiente son parciales/);
    assert.match(slot, /No se incluye en el vencido/);
    assert.match(slot, /distribución superior/);
    assert.match(slot, /Empresas pendientes/);
    assert.doesNotMatch(slot, /Empresa no tiene seguimiento de devoluciones parciales/);
    click("[data-kpis-toggle]"); click("[data-kpis-toggle]");
    assert.equal(requests.length, 1, "Reopening reuses figures for the same filters");
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "exceptions"});
    assert.deepEqual(route, ["List", "CN Reconciliation Exception"]);
    assert.equal(context.frappe.route_options, payload.exceptions.filters);
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "aging"});
    assert.deepEqual(route, ["query-report", "Antiguedad de Saldos"]);
    assert.equal(context.frappe.route_options.from_month, `${controls.year.value}-01-01`);
    assert.equal(context.frappe.route_options.as_of_date, "2026-10-02");
    assert.equal(context.frappe.route_options.balance_type, "Aplicado pendiente de depósito");
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "receivable-aging"});
    assert.equal(context.frappe.route_options.balance_type, "CxC por ajustes");
    await refresh(); // Open figures reload.
    const stale = requests.at(-1);
    controls.year.value = "Todos"; controls.employer.value = "E";
    await refresh();
    const latest = requests.at(-1);
    assert.equal(latest.args.args.year, "Todos");
    assert.equal(latest.args.args.employer, "E");
    stale.resolve({message: {...payload, applications: {net_applied_usd: 99999}}}); await tick();
    assert.doesNotMatch(slot, /99,999/);
    latest.reject(new Error("Unavailable")); await tick();
    assert.match(slot, /No se pudieron cargar las cifras/);
    assert.doesNotMatch(slot, /USD\s*0\.00/);
    const retry = click("[data-kpis-retry]");
    requests.at(-1).resolve({message: payload}); await retry;
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "aging"});
    assert.equal(context.frappe.route_options.from_month, "");
    assert.equal(context.frappe.route_options.to_month, "");
    assert.equal(context.frappe.route_options.employer, "E");
    await refresh();
    requests.at(-1).resolve({message: {...payload, applications: null}}); await tick();
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "receivable-aging"});
    assert.deepEqual(route, ["query-report", "Antiguedad de Saldos"]);
    assert.equal(context.frappe.route_options.balance_type, "CxC por ajustes",
        "Adjustment drill-down must not require application figures");
    handlers["click:[data-kpi-action]"].call({"data-kpi-action": "aging"});
    assert.equal(context.frappe.route_options.balance_type, "CxC por ajustes");
    const denied = context.renderKpis({applications: null, deposits: null, credits: null, exceptions: null, receivables: null});
    assert.equal((denied.match(/No disponible con sus permisos/g) || []).length, 7);
    assert.doesNotMatch(denied, /data-kpi-action|USD\s*0\.00/);
    console.log("OK: seven KPIs including independent receivables, on-demand loading, caching, stale response protection, permissions, warnings, retry and scoped actions.");
})().catch(error => {console.error(error); process.exitCode = 1;});
