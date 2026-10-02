const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

let dialog, calls = [], phase = "preview", html = "", legacy = false, handlers = {};
const storage = new Map();
const options = {source_file: "/private/files/all.xlsx", currency: "USD", manual_fx_rate: 0};
const state = {status: "Vista previa", options, summary: {
    groups: [{employer: "<Empresa>", event_date: "2025-04-15", count: 2, total_usd: 45.04},
        {employer: "NO IDENTIFICADA", event_date: "2025-04-15", count: 1, total_usd: 10}],
    rows: 2, issues: [], issues_count: 0, duplicates: [], duplicates_count: 1, excluded: [], excluded_count: 0,
    unidentified_count: 1,
    file_hash: "FILE-HASH",
}};
function addSections(summary) {
    summary.sections = {};
    for (const [key, unknown] of [["identified", false], ["unidentified", true]]) {
        const belongs = employer => (!employer || employer === "NO IDENTIFICADA") === unknown;
        const groups = summary.groups.filter(group => belongs(group.employer));
        const complementary = (summary.complementary || []).filter(row => belongs(row.employer));
        const deposits = (summary.deposits || []).filter(row => belongs(row.employer));
        summary.sections[key] = {groups, rows: groups.reduce((sum, group) => sum + group.count, 0),
            application_total_usd: groups.reduce((sum, group) => sum + group.total_usd, 0),
            complementary, complementary_count: complementary.length, deposits, deposit_count: deposits.length,
            applications: unknown && groups.length ? [{row: 8, event_date: "2025-04-15", client_name: "<Cliente pendiente>",
                employer_text: "<Empresa original>", loan_number: "123-1", voucher: "AS-1", total_usd: 10,
                description: 'Asiento completo "cobranza" <script> & detalle'}] : []};
    }
}
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
            if (args.method.endsWith("preview_bulk_import")) state.options = {...args.args,
                employer_assignments: JSON.parse(args.args.employer_assignments || "{}")};
            if (args.method.endsWith("get_bulk_import_status")) {
                if (!legacy) addSections(state.summary);
                return {message: state};
            }
            return {message: {token: "token"}};
        },
        ui: {Dialog: function (opts) {
            dialog = this;
            this.opts = opts;
            this.primary = opts.primary_action;
            this.label = opts.primary_action_label;
            this.values = {...options};
            this.props = {};
            this.fields_dict = {result: {$wrapper: {html: value => {html = value;}, append: value => {html += value;}, empty: () => {html = "";},
                on: (event, selector, callback) => {handlers[selector] = callback;}}}};
            this.get_values = () => this.values;
            this.set_values = async values => { this.values = {...values}; };
            this.set_df_property = (field, prop, value) => {this.props[`${field}:${prop}`] = value;};
            this.get_primary_btn = () => ({prop: (key, value) => {this.disabled = value;}});
            this.set_primary_action = (label, action) => {this.label = label; this.primary = action;};
            this.set_secondary_action = action => {this.secondary = action;};
            this.set_secondary_action_label = () => {};
            this.show = () => {};
            this.hide = () => opts.onhide?.();
        }},
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/public/js/accounting_bulk.js"), "utf8"), context);

(async () => {
    context.frappe.credinomina.openAccountingBulk();
    await dialog.primary();
    assert.equal(dialog.label, "Crear importaciones");
    assert.equal(dialog.disabled, false);
    assert.ok(html.includes("Posibles duplicados (se importarán)"));
    assert.ok(html.includes("se importarán todos"));
    assert.ok(html.includes("Cada aplicación tendrá su propia importación"));
    const identified = html.split('data-identification="identified"')[1].split("</section>")[0];
    const unidentified = html.split('data-identification="unidentified"')[1].split("</section>")[0];
    assert.ok(identified.includes("&lt;Empresa&gt;") && !identified.includes("NO IDENTIFICADA"));
    assert.ok(unidentified.includes("&lt;Cliente pendiente&gt;"));
    assert.ok(unidentified.includes("&lt;Empresa original&gt;") && unidentified.includes("AS-1"));
    assert.ok(!unidentified.includes("&lt;Empresa&gt;"));
    assert.ok(unidentified.includes('title="Asiento completo &quot;cobranza&quot; &lt;script&gt; &amp; detalle"'));
    const main = dialog;
    handlers[".cn-change-employer"].call({getAttribute: () => "8"});
    assert.equal(dialog.opts.fields[1].options, "CN Employer");
    await dialog.primary({employer: "Empresa elegida"});
    dialog = main;
    assert.equal(dialog.disabled, true, "Changing the company invalidates confirmation until reanalysis");
    assert.ok(html.includes("selecciones de empresa sin analizar"));
    const confirmations = calls.filter(call => call.method.endsWith("confirm_bulk_import")).length;
    await dialog.primary();
    assert.equal(calls.filter(call => call.method.endsWith("confirm_bulk_import")).length, confirmations);
    dialog.secondary();
    await dialog.primary();
    const reanalysis = calls.filter(call => call.method.endsWith("preview_bulk_import")).at(-1);
    assert.equal(JSON.parse(reanalysis.args.employer_assignments)["8"], "Empresa elegida");
    assert.equal(reanalysis.args.assignments_file_hash, "FILE-HASH");
    assert.equal(dialog.disabled, false);
    assert.ok(!html.includes("Duplicados omitidos"));
    assert.equal(dialog.props["source_file:read_only"], 1);
    assert.ok(html.includes("&lt;Empresa&gt;"));
    assert.ok(html.includes("fecha:2025-04-15"));
    assert.equal(calls[0].args.currency, "USD");
    state.status = "Completado";
    state.created = [{name: "CONTA-A-4-2025-001", employer: "A", event_date: "2025-04-15", rows: 2, total_usd: 45.04},
        {name: "CONTA-NO-IDENTIFICADA", employer: "NO IDENTIFICADA", event_date: "2025-04-15", rows: 1, total_usd: 10}];
    await dialog.primary();
    assert.ok(calls.some(call => call.method.endsWith("confirm_bulk_import")));
    assert.ok(html.includes("/app/cn-accounting-import/CONTA-A-4-2025-001"));
    assert.ok(!html.split('data-completed="identified"')[1].split("</section>")[0].includes("CONTA-NO-IDENTIFICADA"));
    assert.ok(html.split('data-completed="unidentified"')[1].split("</section>")[0].includes("CONTA-NO-IDENTIFICADA"));
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
    dialog.secondary();
    state.status = "Vista previa";
    legacy = true;
    delete state.summary.sections;
    await dialog.primary();
    assert.equal(dialog.disabled, true, "An old cached preview must be refreshed before confirmation");
    assert.ok(html.includes("Pulse Nuevo análisis"));
    legacy = false;
    state.status = "Error";
    state.phase = "create";
    state.resumable = true;
    state.error = "Worker interrumpido";
    state.progress = "Bloques guardados: 1 de 3";
    state.options.employer_assignments = {8: "Empresa conservada"};
    state.options.assignments_file_hash = "FILE-HASH";
    storage.delete("cn-accounting-bulk:tester:employers");
    storage.delete("cn-accounting-bulk:tester");
    context.frappe.credinomina.openAccountingBulk();
    await dialog.opts.fields.find(field => field.fieldname === "recover_batch").click();
    assert.equal(dialog.label, "Reanudar carga");
    assert.ok(html.includes("Bloques guardados: 1 de 3"));
    assert.equal(JSON.parse(storage.get("cn-accounting-bulk:tester:employers")).choices[8], "Empresa conservada");
    await dialog.primary();
    assert.ok(calls.some(call => call.method.endsWith("resume_bulk_import")));
    dialog.secondary();
    await dialog.primary();
    assert.equal(JSON.parse(calls.filter(call => call.method.endsWith("preview_bulk_import")).at(-1).args.employer_assignments)[8], "Empresa conservada");
    console.log("Carga masiva UI: preview, confirmation, escaping, dates, option locking and issues OK");
})().catch(error => {console.error(error); process.exitCode = 1;});
