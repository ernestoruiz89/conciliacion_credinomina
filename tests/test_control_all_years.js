const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const current = new Date().getFullYear(), previous = current - 1, old = current - 12;
const periods = [current, previous, old].map((year, i) => ({name: `P-${year}`, employer: `E-${year}`,
    month: `${year}-04`, reconciliation_mode: "Historica", control_state: "historico_pendiente", applied_usd: 100 * (i + 1)}));
const controls = {}, handlers = {}, calls = [];
let html, url, refresh;
const root = {appendTo() {return this;}, html(value) {html = value; return this;},
    on(event, selector, callback) {handlers[selector] = callback; return this;}};
const context = vm.createContext({URLSearchParams, __: x => x, $: value => typeof value === "string" ? root : {attr: key => value[key]},
    window: {open: value => {url = value;}}, frappe: {
        pages: {"control-credinomina": {}},
        ui: {make_app_page: () => ({main: {}, add_button() {}, set_primary_action(label, cb) {refresh = cb;},
            add_field(df) {const control = {df, value: df.default || "", get_value() {return this.value;},
                refresh() {}, set_input(value) {this.value = value; df.change();}};
                controls[df.fieldname] = control;
                df.change(); // Initialization callbacks must not access uninitialized controls.
                return control;
            },
        })},
        call: async args => {
            calls.push(args);
            const all = args.args.year === "Todos";
            const rows = all ? periods : periods.filter(p => p.month.startsWith(args.args.year));
            return {message: {year: all ? "Todos" : Number(args.args.year), available_years: [current, previous, old],
                periods: rows, totals: {applied_usd: rows.reduce((sum, p) => sum + p.applied_usd, 0)}, work_items: []}};
        },
    }});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8"), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
const matrix = () => html.slice(html.indexOf("Empresas por mes de conciliación"), html.lastIndexOf('<p class="cn-footnote"'));
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({});
    await tick();
    assert.equal(calls.length, 1);
    assert.ok(controls.year.df.options.includes(String(old)));
    assert.ok(!html.includes("data-calendar-year"));
    controls.year.value = "Todos";
    await controls.year.df.change(); await tick();
    assert.ok(html.includes("data-calendar-year"));
    assert.ok(matrix().includes(`value="${current}" selected`));
    assert.ok(matrix().includes(`data-month="${current}-04"`));
    assert.ok(!matrix().includes(`data-month="${previous}-04"`));
    const before = calls.length;
    const totals = html.slice(0, html.indexOf("Empresas por mes de conciliación"));
    handlers["[data-calendar-year]"].call({value: String(previous)});
    assert.equal(calls.length, before); // Local calendar change, not a server year filter.
    assert.equal(html.slice(0, html.indexOf("Empresas por mes de conciliación")), totals);
    assert.ok(matrix().includes(`data-month="${previous}-04"`));
    assert.ok(!matrix().includes(`data-month="${current}-04"`));
    handlers["[data-export]"]();
    assert.equal(new URL(url, "https://example.test").searchParams.get("year"), "Todos");
    await refresh();
    assert.ok(matrix().includes(`value="${previous}" selected`));
    controls.year.value = String(old);
    await controls.year.df.change(); await tick();
    assert.ok(!html.includes("data-calendar-year"));
    assert.ok(matrix().includes(`data-month="${old}-04"`));
    controls.year.value = "Todos";
    await controls.year.df.change(); await tick();
    assert.ok(matrix().includes(`value="${current}" selected`));
    console.log("OK: all-year scope, dynamic years, current-year calendar, independent calendar filter and full-scope export.");
})().catch(error => {console.error(error); process.exitCode = 1;});
