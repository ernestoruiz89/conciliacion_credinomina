const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
let dialog, reloads = 0, calls = [], messages = [], loadCandidates = null;
const candidates = [.08, .10, .13, .16].map((amount, index) => ({name: `APP-${index}`, client_name: `<${index === 0 ? "José " : ""}Cliente ${index}>`,
    client_number: `000${index}`, loan_number: `${index}-1`, event_date: "2025-05-31", adjustable_usd: amount}));
const context = vm.createContext({__: value => value, format_currency: String, setTimeout, frappe: {
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;")},
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {dialog = this; this.options = options; this.values = {}; this.fields_dict = {};
            for (const field of options.fields) if (field.fieldname) this.fields_dict[field.fieldname] = {df: field, grid: {refresh() {}}};
            this.fields_dict.distribution_summary.$wrapper = {html() {}};
            this.fields_dict.applications.grid.wrapper = {on() {}};
            this.fields_dict.client_search.$input = {on: (_event, callback) => {this.searchInput = callback;}, val: () => this.values.client_search};}
        get_value(key) {return this.values[key];}
        show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
    }},
    call: async args => {calls.push(args);
        if (args.method.endsWith("application_candidates") && loadCandidates) return loadCandidates(args);
        return {message: args.method.endsWith("application_candidates") ? candidates
        : args.method.endsWith("get_detail") ? [{related_application: "ORIGINAL", related_import: "IMPORT/1", client_name: "<Cliente original>",
            loan_number: "1-1", application_adjustment_usd: .17}, ...candidates.map(row => ({related_application: row.name,
            related_import: "IMPORT/1", client_name: row.client_name, application_adjustment_usd: row.adjustable_usd}))] : {}};},
    msgprint: value => messages.push(value), show_alert() {},
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
const frm = {doc: {name: "ITEM", modified: "VERSION", docstatus: 1, category: "Ajuste de aplicación", employer: "EMP",
    amount_usd: .64, related_application: "ORIGINAL", application_adjustment_usd: .17},
    is_dirty: () => false, reload_doc: async () => {reloads++;}};
context.frm = frm;
(async () => {
    await vm.runInContext("cn_distribute_applications(frm)", context);
    assert.equal(dialog.options.primary_action_label, "Confirmar distribución adicional");
    const currencyFields = dialog.fields_dict.applications.df.fields.filter(field => field.fieldtype === "Currency");
    assert.deepEqual(Array.from(currencyFields, field => field.options), ["USD", "USD"]);
    dialog.values.accounting_import = "IMPORT";
    await dialog.options.fields.find(field => field.fieldname === "accounting_import").onchange();
    let rows = dialog.fields_dict.applications.df.data;
    assert.equal(rows.length, 4);
    rows.forEach(row => {row.amount_usd = row.adjustable_usd;});
    const filter = value => {dialog.values.client_search = value; dialog.searchInput();};
    const candidateRequests = calls.length;
    filter('JOSE cliente 0');
    assert.equal(dialog.fields_dict.applications.df.data.length, 1);
    assert.equal(dialog.fields_dict.applications.df.data[0].amount_usd, .08);
    dialog.fields_dict.applications.df.data[0].amount_usd = .07;
    filter('0003');
    assert.equal(dialog.fields_dict.applications.df.data[0].application, 'APP-3');
    dialog.fields_dict.applications.df.data[0].amount_usd = 0;
    filter('sin coincidencias');
    assert.equal(dialog.fields_dict.applications.df.data.length, 0);
    filter('');
    rows = dialog.fields_dict.applications.df.data;
    assert.deepEqual(Array.from(rows, row => row.amount_usd), [.07, .10, .13, 0], 'Filtering preserves edits, hidden selections and explicit removals');
    filter('2-1');
    assert.equal(dialog.fields_dict.applications.df.data[0].application, 'APP-2');
    filter('');
    rows = dialog.fields_dict.applications.df.data;
    rows.forEach(row => {row.amount_usd = row.adjustable_usd;});
    filter('0003');
    assert.equal(calls.length, candidateRequests, 'Client search filters locally without new candidate requests');
    assert.equal(frm.doc.application_adjustment_usd, .17, 'Searching does not change saved adjustments');
    await dialog.options.primary_action();
    const request = calls.find(call => call.method.endsWith("application_adjustment_links.distribute"));
    assert.equal(request.args.modified, "VERSION");
    assert.equal(JSON.parse(request.args.rows).length, 4);
    assert.equal(Math.round(JSON.parse(request.args.rows).reduce((sum, row) => sum + row.amount_usd, 0) * 100), 47);
    assert.equal(frm.doc.related_application, "ORIGINAL");
    assert.equal(frm.doc.application_adjustment_usd, .17);
    assert.equal(reloads, 1);
    calls = [];
    dialog.fields_dict.applications.df.data[0].amount_usd = .60;
    await dialog.options.primary_action();
    assert.equal(calls.length, 0);
    assert.ok(messages.at(-1).includes("remanente"));
    let html = "";
    frm.fields_dict = {application_links_preview: {$wrapper: {html: value => {html = value;}}}};
    await vm.runInContext("cn_render_application_links(frm)", context);
    assert.ok(html.includes("ORIGINAL") && html.includes("APP-3"));
    assert.ok(html.includes("&lt;Cliente original>") && !html.includes("<Cliente original>"));
    assert.ok(html.includes("/app/cn-accounting-import/IMPORT%2F1"));
    assert.ok(html.includes("Importación") && html.includes("Remanente US$"));
    frm.doc.docstatus = 2;
    await vm.runInContext("cn_render_application_links(frm)", context);
    assert.ok(html.includes("Partida cancelada") && html.includes("ORIGINAL"));
    frm.doc.docstatus = 1;
    const pending = [];
    loadCandidates = args => new Promise((resolve, reject) => pending.push({args, resolve, reject}));
    await context.cn_distribute_applications(frm);
    const field = dialog.options.fields.find(field => field.fieldname === 'accounting_import');
    dialog.values.accounting_import = 'FIRST';
    let loading = field.onchange();
    await field.onchange();
    assert.equal(pending.length, 1, 'Repeated Link events share the pending request');
    const beforeLoad = calls.length;
    await dialog.options.primary_action();
    assert.equal(calls.length, beforeLoad, 'Cannot distribute while balances are loading');
    pending[0].resolve({message: candidates}); await loading;
    dialog.fields_dict.applications.df.data[0].amount_usd = .07;
    await field.onchange();
    assert.equal(pending.length, 1, 'Blurring the same import does not reload balances');
    assert.equal(dialog.fields_dict.applications.df.data[0].amount_usd, .07);
    dialog.values.accounting_import = 'SECOND';
    const stale = field.onchange();
    dialog.values.accounting_import = 'THIRD';
    loading = field.onchange();
    pending[2].resolve({message: candidates.slice(0, 1)}); await loading;
    pending[1].resolve({message: candidates}); await stale;
    assert.equal(dialog.fields_dict.applications.df.data.length, 1, 'Old responses cannot replace the selected import');
    assert.equal(dialog.fields_dict.applications.df.data[0].amount_usd, .07, 'Import changes preserve selections');
    dialog.values.accounting_import = 'FAILED';
    loading = field.onchange(); pending[3].reject(new Error('Network')); await loading;
    assert.ok(messages.at(-1).includes('reintentar'));
    loading = field.onchange();
    assert.equal(pending.length, 5, 'The same import can be retried after failure');
    dialog.options.onhide();
    pending[4].resolve({message: candidates}); await loading;
    assert.equal(dialog.fields_dict.applications.df.data.length, 0, 'Closed dialogs discard late replies');
    console.log("OK: distribución de 47 centavos entre cuatro aplicaciones, vínculo original, recarga y límite de remanente.");
})().catch(error => {console.error(error); process.exitCode = 1;});
