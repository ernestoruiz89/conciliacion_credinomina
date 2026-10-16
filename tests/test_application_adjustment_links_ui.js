const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
let dialog, reloads = 0, calls = [], messages = [];
const candidates = [.08, .10, .13, .16].map((amount, index) => ({name: `APP-${index}`, client_name: `<Cliente ${index}>`,
    loan_number: `${index}-1`, event_date: "2025-05-31", adjustable_usd: amount}));
const context = vm.createContext({__: value => value, format_currency: String, frappe: {
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;")},
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {dialog = this; this.options = options; this.values = {}; this.fields_dict = {};
            for (const field of options.fields) if (field.fieldname) this.fields_dict[field.fieldname] = {df: field, grid: {refresh() {}}};}
        get_value(key) {return this.values[key];}
        show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
    }},
    call: async args => {calls.push(args); return {message: args.method.endsWith("application_candidates") ? candidates
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
    dialog.values.accounting_import = "IMPORT";
    await dialog.options.fields.find(field => field.fieldname === "accounting_import").onchange();
    const rows = dialog.fields_dict.applications.df.data;
    assert.equal(rows.length, 4);
    rows.forEach(row => {row.amount_usd = row.adjustable_usd;});
    await dialog.options.primary_action();
    const request = calls.find(call => call.method.endsWith("application_adjustment_links.distribute"));
    assert.equal(request.args.modified, "VERSION");
    assert.equal(JSON.parse(request.args.rows).length, 4);
    assert.equal(Math.round(JSON.parse(request.args.rows).reduce((sum, row) => sum + row.amount_usd, 0) * 100), 47);
    assert.equal(frm.doc.related_application, "ORIGINAL");
    assert.equal(frm.doc.application_adjustment_usd, .17);
    assert.equal(reloads, 1);
    calls = [];
    rows[0].amount_usd = .60;
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
    console.log("OK: distribución de 47 centavos entre cuatro aplicaciones, vínculo original, recarga y límite de remanente.");
})().catch(error => {console.error(error); process.exitCode = 1;});
