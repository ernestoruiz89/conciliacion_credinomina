const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

let dialog, calls = [], phase = "preview", html = "";
const storage = new Map();
const options = {source_file: "/private/files/all.xlsx", currency: "USD", manual_fx_rate: 0};
const state = {status: "Vista previa", options, summary: {
    groups: [{employer: "<Empresa>", event_date: "2025-04-15", count: 2, total_usd: 45.04}],
    rows: 2, issues: [], issues_count: 0, duplicates: [], duplicates_count: 1, excluded: [], excluded_count: 0,
}};
const context = vm.createContext({
    __: text => text,
    format_currency: value => `USD ${value}`,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    setTimeout: () => 0, clearTimeout() {},
    frappe: {
        session: {user: "tester"},
        utils: {escape_html: value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")},
        datetime: {str_to_user: value => `fecha:${value}`},
        msgprint() {},
        call: async args => {
            calls.push(args);
            if (args.method.endsWith("get_bulk_import_status")) return {message: state};
            return {message: {token: "token"}};
        },
        ui: {Dialog: function (opts) {
            dialog = this;
            this.opts = opts;
            this.primary = opts.primary_action;
            this.label = opts.primary_action_label;
            this.values = {...options};
            this.props = {};
            this.fields_dict = {result: {$wrapper: {html: value => {html = value;}, empty: () => {html = "";}}}};
            this.get_values = () => this.values;
            this.set_values = async values => { this.values = {...values}; };
            this.set_df_property = (field, prop, value) => {this.props[`${field}:${prop}`] = value;};
            this.get_primary_btn = () => ({prop: (key, value) => {this.disabled = value;}});
            this.set_primary_action = (label, action) => {this.label = label; this.primary = action;};
            this.set_secondary_action = action => {this.secondary = action;};
            this.set_secondary_action_label = () => {};
            this.show = () => {};
            this.hide = () => opts.onhide();
        }},
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/public/js/accounting_bulk.js"), "utf8"), context);

(async () => {
    context.frappe.credinomina.openAccountingBulk();
    await dialog.primary();
    assert.equal(dialog.label, "Crear importaciones");
    assert.equal(dialog.disabled, false);
    assert.equal(dialog.props["source_file:read_only"], 1);
    assert.ok(html.includes("&lt;Empresa&gt;"));
    assert.ok(html.includes("fecha:2025-04-15"));
    assert.equal(calls[0].args.currency, "USD");
    state.status = "Completado";
    state.created = [{name: "CONTA-A-4-2025-001", employer: "A", event_date: "2025-04-15", rows: 2, total_usd: 45.04}];
    await dialog.primary();
    assert.ok(calls.some(call => call.method.endsWith("confirm_bulk_import")));
    assert.ok(html.includes("/app/cn-accounting-import/CONTA-A-4-2025-001"));
    dialog.secondary();
    assert.equal(dialog.label, "Analizar archivo");
    assert.equal(dialog.props["source_file:read_only"], 0);
    state.status = "Vista previa";
    state.summary.issues_count = 1;
    state.summary.issues = [{row: 4, reason: "<Crédito ambiguo>"}];
    await dialog.primary();
    assert.equal(dialog.disabled, true);
    assert.ok(html.includes("&lt;Crédito ambiguo&gt;"));
    dialog.secondary();
    state.summary = {groups: [], rows: 0, issues_count: 0, issues: [], duplicates_count: 0, duplicates: [], excluded_count: 0, excluded: [],
        complementary_count: 1, complementary: [{row: 2, classification: "<Movimiento interno>", reason: "Revisar", employer: ""}]};
    await dialog.primary();
    assert.equal(dialog.disabled, false, "Files with only review drafts must be importable");
    assert.ok(html.includes("&lt;Movimiento interno&gt;"));
    assert.ok(html.includes("Pendiente de identificar"));
    state.status = "Completado";
    state.created = [{doctype: "CN Complementary Item", name: "COMP-1", event_date: "2025-04-15", rows: 1, total_usd: 1}];
    await dialog.primary();
    assert.ok(html.includes("/app/cn-complementary-item/COMP-1"));
    dialog.secondary();
    state.status = "Vista previa";
    state.summary.complementary_count = 0;
    state.summary.deposit_count = 1;
    state.summary.deposits = [{row: 2, employer: "<Empresa>", reference: "DEP", amount: 100, currency: "USD"}];
    await dialog.primary();
    assert.equal(dialog.disabled, false, "Files with only deposits must be importable");
    assert.ok(html.includes("Depósitos detectados"));
    assert.ok(html.includes("&lt;Empresa&gt;"));
    state.status = "Completado";
    state.created = [{doctype: "CN Remittance Allocation", name: "DEP-4-2025-0001", event_date: "2025-04-15", rows: 1, total_usd: 100}];
    await dialog.primary();
    assert.ok(html.includes("/app/cn-remittance-allocation/DEP-4-2025-0001"));
    console.log("Carga masiva UI: preview, confirmation, escaping, dates, option locking and issues OK");
})().catch(error => {console.error(error); process.exitCode = 1;});
