const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
let dialog, hidden = false, calls = [], handlers = {}, fieldsLocked = {}, applied;
const wrapper = {
    content: "",
    html(value) { this.content = value; handlers = {}; return this; },
    find(selector) {
        return {on(event, callback) { handlers[`${selector}:${event}`] = callback; return this; },
            removeClass() { return this; }};
    },
};
const primary = {disabled: false, prop(name, value) { this[name] = value; return this; }};
const row = {related_case_type: "Aplicación", related_case_id: "ROW", employer: "EMP",
    client_name: "<img src=x onerror=alert(1)>", client_number: "12", loan_number: "100-1",
    period: "PER", date: "2026-09-30", amount_usd: 100, status: "Pendiente", reference: "R1"};
const values = {related_case_type: "Aplicación", related_case_id: "ROW", employer: "EMP", period: "PER",
    source_import: "IMP", source_row: 15, collection_row_id: "CLAIM", client_name: "Ana",
    client_number: "12", loan_number: "100-1", related_case_summary: "Aplicación · Ana"};
const context = {
    __: value => value, $: value => value, format_currency: value => `${value.toFixed(2)} USD`,
    frappe: {
        ui: {form: {on() {}}, Dialog: class {
            constructor(options) {
                this.options = options;
                this.values = {};
                this.fields_dict = {results: {$wrapper: wrapper}};
                options.fields.forEach(f => { if (f.fieldname) this.values[f.fieldname] = f.default; });
                // Frappe may call onchange during construction.
                options.fields[0].onchange();
                dialog = this;
            }
            get_value(name) { return this.values[name]; }
            get_primary_btn() { return primary; }
            show() {}
            hide() { hidden = true; }
        }},
        utils: {escape_html: text => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
        datetime: {str_to_user: value => `LOCAL:${value}`},
        async call(args) {
            calls.push(args);
            return {message: args.method.endsWith("get_related_cases") ? {rows: [row], start: 0, has_more: false} : values};
        },
        show_alert() {}, msgprint() {}, confirm(message, callback) { return callback(); },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_exception/cn_reconciliation_exception.js"), "utf8"), context);

async function run() {
    const frm = {doc: {employer: "EMP", period: "PER", source_import: "OLD", amount_usd: 10, description: "Diferencia"},
        async set_value(value) { applied = value; Object.assign(this.doc, value); },
        set_df_property(field, property, value) { fieldsLocked[field] = value; }};
    context.cn_exception_open_case_picker(frm);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls[0].args.kind, "Aplicación");
    assert.equal(primary.disabled, true);
    assert(wrapper.content.includes("&lt;img"));
    assert(!wrapper.content.includes("<img"));
    assert(wrapper.content.includes("LOCAL:2026-09-30"));
    assert(wrapper.content.includes("100.00 USD"));
    const chosen = {attr() { return "0"; }, addClass() { return this; },
        find() { return {prop() {}}; }};
    handlers["tr[data-case-index]:click"].call(chosen);
    assert.equal(primary.disabled, false);
    await dialog.options.primary_action();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(hidden, true);
    assert.equal(applied.related_case_id, "ROW");
    assert.equal(frm.doc.amount_usd, 10);
    assert.equal(frm.doc.description, "Diferencia");
    assert.equal(fieldsLocked.loan_number, true);
    assert.equal(calls[1].args.period, "PER");
    // Selection edits only the form: no save, submit or reconciliation API.
    assert.equal(calls.length, 2);

    context.cn_exception_open_case_picker(frm);
    await new Promise(resolve => setImmediate(resolve));
    handlers["tr[data-case-index]:click"].call(chosen);
    dialog.options.fields.find(f => f.fieldname === "search").onchange();
    assert.equal(primary.disabled, true);
    assert(wrapper.content.includes("Buscar casos"));
    console.log("OK: picker links, preserves amount, escapes HTML, formats dates and invalidates changed filters.");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
