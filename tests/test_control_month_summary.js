const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
assert.match(source, /\.cn-period-card \{ font-size: 14px; line-height: 1\.5; \}/);
const cardTypography = source.match(/((?:\s*\.cn-period-card \.[\w-]+,)+\s*\.cn-period-card \.[\w-]+) \{ font-size: inherit; line-height: inherit; \}/);
assert.ok(cardTypography, "Month cards must override small calendar fonts");
for (const name of ["cn-cell-sub", "cn-cell-cycle", "cn-cell-gap", "cn-cell-credit", "cn-badge", "cn-period-remark", "cn-period-card-amounts", "cn-period-card-open"]) {
    assert.ok(cardTypography[1].includes(`.cn-period-card .${name}`), `Readable font for ${name}`);
}
assert.match(source, /\.cn-period-card \.cn-period-card-name \{ font-size: 16px; \}/);
assert.match(source, /\.cn-period-card \.cn-cell-amount,\s*\.cn-period-card \.cn-period-card-amounts strong \{ font-size: 18px; line-height: 1\.4; \}/);
const handlers = {};
let html, refreshPage, dialog;
const root = {appendTo() { return this; }, html(value) { html = value; return this; },
    on(event, selector, callback) { handlers[selector] = callback; return this; }};
const periods = [
    {name: "P1", employer: "E", month: "2026-09", reconciliation_mode: "Operativa", collection_cycle: "Primera quincena",
        control_state: "conciliado", remitted_usd: 10.1, deducted_usd: 10.1, applied_usd: 10.1,
        remark: "Primera entrega\nPendiente <soporte> & validación"},
    {name: "P2", employer: "E", month: "2026-09", reconciliation_mode: "Operativa", collection_cycle: "Segunda quincena",
        control_state: "en_transito", remitted_usd: 0, deducted_usd: 20.2, applied_usd: 20.2, employer_gap_usd: 20.2},
    {name: "P3", employer: "F", month: "2026-09", reconciliation_mode: "Historica", control_state: "historico_pendiente", applied_usd: 70},
    {name: "P4", employer: "E", month: "2026-10", reconciliation_mode: "Operativa", control_state: "en_transito", applied_usd: 80},
];
const data = {year: 2026, periods, totals: {}};
const context = vm.createContext({
    __: value => value, $: value => typeof value === "string" ? root : {attr: name => value[name]},
    frappe: {pages: {"control-credinomina": {}}, datetime: {str_to_user: value => value},
        call: async () => ({message: data}), ui: {
            make_app_page: () => ({main: {}, add_field: df => ({df, refresh() {}, set_input() {}, get_value: () => df.fieldname === "year" ? "2026" : ""}),
                add_button() {}, set_primary_action(label, fn) { refreshPage = fn; }}),
            Dialog: class {
                constructor(options) { dialog = this; this.options = options; this.events = {}; this.wrapper = {
                    html: value => {this.html = value;}, on: (event, selector, callback) => {this.events[selector] = callback;}}; }
                get_field() {return {$wrapper: this.wrapper};} show() {this.shown = true;} hide() {this.shown = false;}
            },
        },
    },
});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.helpers = {summarizeMonth, renderMonthSummary, renderPeriodCard}; })();"), context);
const {summarizeMonth, renderMonthSummary} = context.helpers;
const total = summarizeMonth(periods.slice(0, 2));
assert.equal(total.deducted_usd, 30.3);
assert.equal(total.remitted_usd, 10.1);
assert.equal(total.employer_gap_usd, 20.2);
assert.equal(total.state, "parcial");
assert.equal(summarizeMonth([{...periods[0], control_state: "historico_excedente"}, periods[1]]).state, "excedente");
assert.equal(summarizeMonth([{...periods[0], control_state: "historico_excepcion"}, periods[1]]).state, "diferencia");
assert.equal(summarizeMonth([periods[0], {...periods[1], control_state: "conciliado"}]).state, "conciliado");
const mixed = renderMonthSummary([periods[0], periods[2]], '<Empresa>', '2026-09');
assert.ok(mixed.includes("Deducido operativo"));
assert.ok(!mixed.includes("Remitido / deducido"));
assert.ok(mixed.includes("&lt;Empresa&gt;"));
const original = JSON.stringify(periods);
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({});
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(html.includes("data-summary checked"));
    assert.equal((html.match(/data-month="/g) || []).length, 3); // Separate companies and months.
    assert.ok(!html.includes('data-period="P1"'));
    assert.ok(html.includes("2 períodos"));
    handlers["[data-month]"].call({"data-employer": "E", "data-month": "2026-09"});
    assert.ok(dialog.shown);
    assert.ok(dialog.html.includes('data-month-period="P1"'));
    assert.ok(dialog.html.includes('data-month-period="P2"'));
    assert.ok(!dialog.html.includes('data-month-period="P3"'));
    assert.ok(dialog.html.includes('class="cn-period-cards"'));
    assert.equal((dialog.html.match(/class="cn-period-card /g) || []).length, 2);
    assert.ok(!dialog.html.includes("<table"));
    assert.ok(dialog.html.includes("Primera entrega\nPendiente &lt;soporte&gt; &amp; validación"));
    assert.ok(dialog.html.includes("<strong>Observaciones</strong>"));
    assert.ok(!dialog.html.includes("<strong>Remark</strong>"));
    assert.ok(!dialog.html.includes("<soporte>"));
    assert.equal((dialog.html.match(/class="cn-period-remark"/g) || []).length, 1);
    assert.ok(dialog.html.includes("Primera quincena") && dialog.html.includes("Segunda quincena"));
    const selector = dialog;
    assert.equal(selector.options.animate, false);
    selector.events["[data-month-period]"].call({"data-month-period": "P2"});
    assert.equal(selector.shown, false);
    assert.equal(dialog.options.primary_action_label, "Abrir período");
    assert.ok(dialog.shown && dialog.html.includes('class="cn-dialog"'));
    const detailHtml = dialog.html;
    const periodDetail = dialog;
    assert.equal(periodDetail.options.animate, false, "Return to month must not leave a transitioning period dialog open");
    const monthHtml = selector.html;
    assert.equal(periodDetail.options.secondary_action_label, "Volver al mes");
    periodDetail.options.secondary_action();
    assert.equal(periodDetail.shown, false);
    assert.equal(selector.shown, true);
    assert.equal(selector.html, monthHtml);
    selector.events["[data-month-period]"].call({"data-month-period": "P1"});
    assert.equal(selector.shown, false);
    assert.ok(dialog.html.includes("Primera entrega"));
    dialog.options.secondary_action();
    assert.equal(selector.shown, true);
    handlers["[data-period]"].call({"data-period": "P2"});
    assert.equal(dialog.html, detailHtml); // Exactly the normal detailed modal.
    assert.equal(dialog.options.secondary_action_label, undefined); // No unrelated month when opened directly.
    const historicalCard = context.helpers.renderPeriodCard({...periods[2], name: '<Period>', historical_scope: "Fecha exacta", historical_application_date: "2026-09-15"});
    assert.ok(historicalCard.includes("2026-09-15"));
    assert.ok(historicalCard.includes("&lt;Period&gt;") && !historicalCard.includes("<Period>"));
    assert.ok(!historicalCard.includes(">Deducido<"));
    for (const [state, label] of [["historico_conciliado", "Conciliado"], ["historico_excepcion", "Con diferencia"]]) {
        periods[2].control_state = state;
        handlers["[data-month]"].call({"data-employer": "F", "data-month": "2026-09"});
        assert.ok(dialog.html.includes(`>${label}</span>`));
        assert.ok(!/Histórico (conciliado|con excepción)/i.test(dialog.html));
    }
    periods[2].control_state = "historico_pendiente";
    handlers["[data-period]"].call({"data-period": "P1"});
    assert.ok(dialog.html.includes("Primera entrega\nPendiente &lt;soporte&gt; &amp; validación"));
    handlers["[data-summary]"].call({checked: false});
    assert.ok(!html.includes("data-summary checked"));
    assert.ok(html.includes('data-period="P1"') && html.includes('data-period="P2"'));
    refreshPage();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(!html.includes("data-summary checked")); // Refresh preserves the user's choice.
    handlers["[data-summary]"].call({checked: true});
    assert.ok(html.includes("data-summary checked"));
    assert.equal(JSON.stringify(periods), original);
    data.periods = [];
    refreshPage();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(html.includes("No hay períodos"));
    console.log("OK: monthly summary default, cents, mixed modes, pending states, drilldown and detail toggle.");
})().catch(error => {console.error(error); process.exitCode = 1;});
