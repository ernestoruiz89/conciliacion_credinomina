const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const dialogs = [], calls = [], buttons = {}, messages = [];
let writable = true, selection = [], refreshed = 0;
class Element {
    constructor() {this.children = new Map(); this.htmlWrites = 0;}
    find(selector) {
        if (!this.children.has(selector)) this.children.set(selector, new Element());
        return this.children.get(selector);
    }
    html(value) {this.content = value; this.htmlWrites++; return this;}
    text(value) {this.value = value; return this;}
    prop(name, value) {this[name] = value; return this;}
    hide() {this.hidden = true; return this;}
    show() {this.hidden = false; return this;}
    on(_event, _selector, handler) {this.click = handler;}
}
const context = vm.createContext({__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]), frappe: {
    listview_settings: {}, model: {can_write: () => writable},
    utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    msgprint: message => messages.push(message),
    ui: {Dialog: function(options) {
        Object.assign(this, options);
        this.$wrapper = new Element();
        this.fields_dict = {results: {$wrapper: new Element()}};
        this.show = () => {this.shown = true;};
        this.hide = () => this.onhide?.();
        this.disable_primary_action = () => {this.disabled = true;};
        dialogs.push(this);
    }},
    call: async args => {
        calls.push(args);
        if (args.method.endsWith("list_periods")) return {message: selection.length ? [] : [
            {name: "P1", employer: "<Empresa>"}, {name: "P2", employer: "Empresa"}, {name: "P3", employer: "Empresa"}]};
        assert.ok(args.method.endsWith("close_reconciled_period"));
        assert.equal(args.type, "POST");
        assert.equal(args.silent, true, "Per-period validation dialogs must not interrupt the batch");
        const dialog = dialogs[0], wrapper = dialog.fields_dict.results.$wrapper;
        assert.equal(dialog.disabled, true);
        assert.equal(wrapper.find("[data-close-dismiss]").disabled, true);
        assert.equal(dialog.$wrapper.find(".modal-header .btn-modal-close").hidden, true);
        // A second action while a request is running must not send another close.
        await dialog.primary_action();
        if (args.args.period_name === "P2") throw {responseJSON: {
            _server_messages: JSON.stringify([JSON.stringify({message: "Hay <b>depósitos pendientes</b>."})]),
        }};
        return {message: {status: "Cerrado"}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period_list.js"), "utf8"), context);
const listview = {page: {add_inner_button: (label, handler) => {buttons[label] = handler;}},
    get_checked_items: () => selection,
    get_args: () => ({filters: [["CN Reconciliation Period", "payroll_month", "=", "2026-10-01"]]}),
    meta: {fields: []}, list_view_settings: {}, setup_columns() {}, render_header() {}, refresh() {refreshed++;}};
(async () => {
    const settings = context.frappe.listview_settings["CN Reconciliation Period"];
    settings.onload(listview);
    await buttons["Cerrar períodos conciliados"]();
    assert.deepEqual(JSON.parse(calls[0].args.filters), listview.get_args().filters);
    const dialog = dialogs[0], wrapper = dialog.fields_dict.results.$wrapper;
    assert.equal(dialog.static, true);
    assert.ok(wrapper.content.includes("incluyendo otras páginas"));
    assert.ok(wrapper.content.includes("&lt;Empresa>"));
    assert.ok(!wrapper.content.includes("<Empresa>"));
    await dialog.primary_action();
    assert.deepEqual(calls.slice(1).map(call => call.args.period_name), ["P1", "P2", "P3"],
        "Failure must not stop the next period or retry a successful one");
    assert.equal(wrapper.find('[data-close-row="1"]').find("[data-reason]").value, "Hay depósitos pendientes.");
    assert.equal(wrapper.find('[data-close-row="2"]').find("[data-state]").value, "Cerrado");
    assert.ok(wrapper.find("[data-close-summary]").value.includes("2 cerrados · 1 no cerrados"));
    assert.equal(wrapper.find("[data-close-dismiss]").disabled, false);
    assert.equal(dialog.$wrapper.find(".modal-header .btn-modal-close").hidden, false);
    assert.equal(wrapper.htmlWrites, 1, "Keep the result table and scroll position during updates");
    assert.equal(refreshed, 1);
    await dialog.primary_action();
    assert.equal(calls.length, 4, "Completed batch must not run twice");
    await buttons["Cerrar períodos conciliados"]();
    assert.equal(dialogs.length, 1, "Keep final reasons available until explicit dismissal");
    dialog.hide();
    selection = [{name: "SELECTED"}];
    await buttons["Cerrar períodos conciliados"]();
    assert.deepEqual(JSON.parse(calls.at(-1).args.period_names), ["SELECTED"]);
    assert.deepEqual(JSON.parse(calls.at(-1).args.filters), []);
    assert.ok(messages.at(-1).includes("No hay períodos conciliados"));
    writable = false;
    delete buttons["Cerrar períodos conciliados"];
    settings.onload(listview);
    assert.equal(buttons["Cerrar períodos conciliados"], undefined);
    console.log("OK: scoped list closure, independent failures, final reasons, safe results and duplicate prevention.");
})().catch(error => {console.error(error); process.exitCode = 1;});
