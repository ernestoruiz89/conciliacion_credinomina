const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const base = "credinomina_reconciliation/conciliacion_credinomina/doctype/";
let dialog, calls = [], routes = [], rowHandlers = {}, properties = {}, buttons = {}, failure = false;
const wrapper = {html(value) { this.content = value; rowHandlers = {}; return this; }, find(selector) {
    return {on(event, callback) { rowHandlers[selector + ":" + event] = callback; return this; }, prop() { return this; }};
}};
const primary = {prop(name, value) { this[name] = value; return this; }};
const context = vm.createContext({__: text => text, $: value => value, format_currency: (n, c) => `${c} ${n.toFixed(2)}`,
    frappe: {ui: {form: {on() {}}, Dialog: class {
        constructor(options) {
            this.options = options; this.values = {};
            options.fields.forEach(field => { if (field.fieldname) this.values[field.fieldname] = field.default; });
            this.fields_dict = {results: {$wrapper: wrapper}};
            dialog = this;
            options.fields.find(field => field.fieldname === "voucher")?.onchange();
        }
        get_value(field) { return this.values[field]; }
        get_primary_btn() { return primary; }
        show() {} hide() { this.hidden = true; }
    }}, model: {can_create: () => true}, session: {user: "Administrator"},
    utils: {escape_html: value => value.replaceAll("<", "&lt;").replaceAll("&lt;img", "&lt;img")},
    datetime: {str_to_user: value => `LOCAL:${value}`},
    set_route: (...args) => routes.push(args), msgprint() {}, show_alert() {},
    async call(args) {
        calls.push(args);
        if (failure && args.method.endsWith("verify_and_resolve")) throw new Error("Server error");
        if (args.method.endsWith("get_item_exception")) return {message: null};
        if (args.method.endsWith("create_item_exception")) return {message: "CN-EXC-2026-0001"};
        if (args.method.endsWith("get_registration_candidates")) return {message: [
            {type: "CN Complementary Item", name: "LEDGER", voucher: "V1", employer: "REPSA",
                source_date: "2025-09-30", original_amount: -0.37, currency: "NIO", amount_usd: -0.01,
                source_row: 3, description: "<img src=x> Ledger"}]};
        return {message: {status: "Resuelta"}};
    },
}});
for (const file of ["cn_complementary_item/cn_complementary_item.js", "cn_reconciliation_exception/cn_reconciliation_exception.js"])
    vm.runInContext(fs.readFileSync(base + file, "utf8"), context);
const item = {doc: {name: "COMP", docstatus: 1, category: "Diferencia por tolerancia"},
    is_new: () => false, is_dirty: () => false, async save() { calls.push("save"); }, async reload_doc() { calls.push("reload"); },
    add_custom_button: (label, callback) => { buttons[label] = callback; }};
const exception = {doc: {name: "EXC", complementary_item: "COMP", docstatus: 0, core_voucher: "V1"},
    is_new: () => false, is_dirty: () => true, async save() { calls.push("save"); }, async reload_doc() { calls.push("reload"); },
    set_df_property: (field, property, value) => { properties[field + ":" + property] = value; }, toggle_display() {},
    set_intro() {}, get_perm: () => true, add_custom_button: item.add_custom_button};

async function run() {
    context.cn_comp_add_exception_button(item);
    assert(buttons["Crear excepción"]); // Also available for a read-only automatic item.
    await buttons["Crear excepción"]();
    assert.equal(dialog.options.title, "Registrar ajuste en el core");
    assert.equal(dialog.values.assigned_to, "Administrator");
    assert(dialog.options.fields.find(field => field.fieldname === "commitment_date").reqd);
    const creation = dialog;
    await Promise.all([creation.options.primary_action({assigned_to: "Administrator", commitment_date: "2026-10-05"}),
        creation.options.primary_action({assigned_to: "Administrator", commitment_date: "2026-10-05"})]);
    assert.equal(calls.filter(call => call.method?.endsWith("create_item_exception")).length, 1);
    assert.equal(routes.at(-1)[2], "CN-EXC-2026-0001");
    item.doc.accounting_exception = "CN-EXC-2026-0001";
    context.cn_comp_add_exception_button(item);
    buttons["Ver excepción"]();
    assert.equal(routes.at(-1)[2], "CN-EXC-2026-0001");
    calls = [];
    context.cn_exception_core_buttons(exception);
    assert.equal(properties["amount_usd:read_only"], 1);
    assert(!properties["status:options"].includes("Resuelta"));
    await buttons["Verificar asiento y resolver"]();
    assert.equal(calls[0], "save");
    assert(wrapper.content.includes("&lt;img"));
    assert(!wrapper.content.includes("<img"));
    assert(wrapper.content.includes("LOCAL:2025-09-30"));
    assert(wrapper.content.includes("USD -0.01"));
    assert.equal(primary.disabled, true);
    const chosen = {attr: () => "0", find: () => ({prop() {}})};
    rowHandlers["tr[data-core-index]:click"].call(chosen);
    assert.equal(primary.disabled, false);
    failure = true;
    await assert.rejects(dialog.options.primary_action({voucher: "V1", resolution: "Verified"}), /Server error/);
    assert.equal(primary.disabled, false);
    failure = false;
    await dialog.options.primary_action({voucher: "V1", resolution: "Verified"});
    assert.equal(calls.at(-2).args.evidence_name, "LEDGER");
    assert.equal(calls.at(-1), "reload");
    await context.cn_exception_verify_core(exception);
    rowHandlers["tr[data-core-index]:click"].call(chosen);
    dialog.options.fields.find(field => field.fieldname === "voucher").onchange();
    assert.equal(primary.disabled, true);
    assert(wrapper.content.includes("Buscar registro"));
    console.log("OK: native exception links, required planning, automatic-item button, save-first, signed columns, dates, escaping, retry and stale selection.");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
