const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let dialog, html = "", disabled, selected, selection, renders = 0;
const requests = [];
const wrapper = {empty() {html = ""; selection = undefined; renders++;}, html(value) {html = value; selection = undefined; renders++;}, find() {return {val: () => selection};}};
const context = vm.createContext({__: text => text, format_currency: value => String(value), frappe: {
    ui: {form: {on() {}}, Dialog: function(options) {
        Object.assign(this, options);
        this.fields_dict = {applications: {$wrapper: wrapper}};
        this.get_value = () => selected;
        this.get_primary_btn = () => ({prop: (_key, value) => {disabled = value;}});
        this.show = () => {};
        this.hide = () => {};
        dialog = this;
    }},
    utils: {escape_html: text => text.replace(/</g, "&lt;")}, datetime: {str_to_user: text => text},
    call: args => new Promise((resolve, reject) => requests.push({args, resolve, reject})), msgprint() {},
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
const changes = [];
const frm = {doc: {name: "ITEM", employer: "EMP", amount_usd: -30}, is_dirty: () => false,
    set_value: async values => changes.push(values), save: async () => {}};
const row = (name, amount) => ({name, client_name: name, adjustable_usd: amount});
(async () => {
    await context.cn_select_original_application(frm);
    assert.equal(disabled, true);
    const field = dialog.fields[0];
    assert.equal(field.get_query().filters.employer, "EMP");
    assert.equal(field.get_query().query, "credinomina_reconciliation.accounting_review.application_import_query");
    selected = "IMPORT";
    let pending = field.onchange();
    await field.onchange();
    assert.equal(requests.length, 1, "duplicate change while loading must share the current request");
    requests.shift().resolve({message: [row("PAID", 0), row("VALID", 20)]});
    await pending;
    assert.ok(!html.includes("PAID"));
    assert.ok(html.includes("VALID"));
    assert.equal(disabled, false);
    selection = "0";
    const rendered = renders;
    await field.onchange();
    assert.equal(requests.length, 0, "blur with the same import must not reload candidates");
    assert.equal(renders, rendered, "first row click must not replace the table");
    assert.equal(selection, "0", "the first selection must survive the Link blur event");
    await dialog.primary_action();
    assert.equal(changes[0].related_application, "VALID");
    assert.equal(changes[0].application_adjustment_usd, 20);
    selected = "EMPTY";
    pending = field.onchange();
    assert.equal(disabled, true);
    requests.shift().resolve({message: []});
    await pending;
    assert.ok(html.includes("No hay aplicaciones válidas"));
    assert.equal(disabled, true);
    // A failed lookup can be retried without changing the import.
    selected = "RETRY";
    pending = field.onchange();
    requests.shift().reject(new Error("lookup failed"));
    await assert.rejects(pending, /lookup failed/);
    pending = field.onchange();
    requests.shift().resolve({message: [row("RETRIED", 10)]});
    await pending;
    assert.ok(html.includes("RETRIED"));
    // Switching away and back must not display an earlier response for the same import.
    selected = "A";
    const old = field.onchange();
    selected = "B";
    const middle = field.onchange();
    selected = "A";
    const current = field.onchange();
    requests[2].resolve({message: [row("CURRENT", 10)]});
    await current;
    requests[0].resolve({message: [row("STALE", 10)]});
    requests[1].resolve({message: [row("OTHER", 10)]});
    await Promise.all([old, middle]);
    assert.ok(html.includes("CURRENT") && !html.includes("STALE") && !html.includes("OTHER"));
    selected = "";
    await field.onchange();
    assert.equal(html, "");
    assert.equal(disabled, true);
    console.log("OK: single-click selection survives duplicate Link events, retries, empty state and stale responses.");
})().catch(error => {console.error(error); process.exitCode = 1;});
