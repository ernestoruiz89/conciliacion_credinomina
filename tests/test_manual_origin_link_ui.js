const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
let dialog, reloads = 0, differences = [], calls = [];
const handlers = {}, messages = [];
class Dialog {
    constructor(options) {
        dialog = this; this.options = options; this.values = {}; this.fields_dict = {};
        for (const df of options.fields) {
            this.values[df.fieldname] = df.default || "";
            const field = {df, $wrapper: {html(value) {field.html = value;}, empty() {field.html = "";}}};
            field.grid = {refresh() {}, get_selected_children: () => (field.df.data || []).filter(row => row.__checked)};
            this.fields_dict[df.fieldname] = field;
        }
    }
    get_value(name) {return this.values[name];}
    set_primary_action(label, action) {this.options.primary_action_label = label; this.options.primary_action = action;}
    disable_primary_action() {} enable_primary_action() {} show() {} hide() {}
}
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on: (name, events) => handlers[name] = events}, Dialog}, utils: {escape_html: value => String(value).replaceAll("<", "&lt;")},
    msgprint: value => messages.push(value), show_alert() {},
    async call(request) {
        calls.push(request);
        if (request.method.endsWith("candidates")) return {message: request.args.kind === "internal"
            ? Array.from({length: 565}, (_, i) => ({name: `I${i}`, source_date: "2026-10-13", source_credit: 1, source_currency: "USD"}))
            : [{name: "CORE", deposit_date: "2026-10-13", deposit_amount: 565, deposit_currency: "USD"}]};
        if (request.method.endsWith("preview")) return {message: {count: request.args.selection.length, currency: "USD", total: 565, deposit_amount: 565,
            difference: differences.length ? 1 : 0, issues: differences, token: "SERVER-TOKEN"}};
        return {message: {status: differences.length ? "Pendiente de revisión" : "Vinculado"}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
context.frm = {doc: {name: "MANUAL", docstatus: 1, deposit_reference: "REF"}, is_dirty: () => false, reload_doc: async () => reloads++};
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    vm.runInContext("linkAccountingOrigins(frm)", context);
    await tick();
    assert.equal(dialog.fields_dict.rows.df.data.length, 565);
    dialog.fields_dict.select_all.df.click();
    assert.equal(dialog.fields_dict.rows.grid.get_selected_children().length, 565);
    await dialog.options.primary_action();
    assert.equal(calls.at(-1).method.split(".").at(-1), "preview");
    assert.equal(dialog.options.primary_action_label, "Vincular al depósito");
    // A selection change requires a fresh server comparison, never the old token.
    dialog.fields_dict.rows.df.data[0].__checked = 0;
    await dialog.options.primary_action();
    assert.equal(calls.at(-1).method.split(".").at(-1), "preview");
    await dialog.options.primary_action();
    assert.ok(messages.at(-1).includes("Indique cómo"));
    dialog.values.reason = "Comprobante verificado";
    await dialog.options.primary_action();
    assert.equal(calls.at(-1).method.split(".").at(-1), "link");
    assert.equal(calls.at(-1).args.token, "SERVER-TOKEN");
    assert.equal(reloads, 1);

    vm.runInContext("linkAccountingOrigins(frm)", context);
    await tick();
    dialog.values.kind = "Depósito del core";
    dialog.fields_dict.kind.df.onchange();
    assert.equal(dialog.fields_dict.rows.df.data.length, 0);
    await dialog.fields_dict.search.df.click();
    dialog.fields_dict.rows.df.data[0].__checked = 1;
    differences = ["No coincide referencia <original>"];
    await dialog.options.primary_action();
    assert.equal(dialog.options.primary_action_label, "Dejar para revisión");
    assert.ok(dialog.fields_dict.result.html.includes("&lt;original>"));
    dialog.values.reason = "Revisar con comprobante";
    await dialog.options.primary_action();
    assert.equal(calls.at(-1).args.kind, "deposit");
    assert.equal(calls.at(-1).args.selection[0], "CORE");
    assert.equal(reloads, 2);
    assert.equal(typeof handlers["CN Remittance Allocation"].link_accounting_origins, "function");
    console.log("Manual origin modal: 565 selections, fresh preview, notes, core deposits and pending differences OK");
})().catch(error => {console.error(error); process.exitCode = 1;});
