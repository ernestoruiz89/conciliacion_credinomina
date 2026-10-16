const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const dialogs = [], calls = [], messages = [], actions = {};
let writable = true, refreshed = 0;
const context = vm.createContext({__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]), frappe: {
    listview_settings: {}, model: {can_write: () => writable}, datetime: {now_date: () => "2026-10-16"},
    utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    show_alert() {}, msgprint: text => messages.push(text),
    ui: {Dialog: function(options) {
        Object.assign(this, options);
        this.fields_dict = Object.fromEntries(options.fields.filter(f => f.fieldname).map(df => [df.fieldname,
            {df, $wrapper: {html(value) {this.content = value;}, on(_event, _selector, fn) {this.click = fn;}}}]));
        this.show = () => {};
        this.hide = () => this.onhide?.();
        this.disable_primary_action = () => {this.disabled = true;};
        this.enable_primary_action = () => {this.disabled = false;};
        this.set_df_property = () => {};
        dialogs.push(this);
    }},
    call: async args => {
        calls.push(args);
        if (args.method.endsWith("list_periods")) return {message: [
            {name: "FAILED", employer: "<Empresa>"}, {name: "MISSING", employer: "Empresa 2"}, {name: "GOOD", employer: "Empresa 3"}]};
        if (args.method.endsWith("reconcile_period")) {
            if (args.args.period_name === "FAILED") throw new Error("<Error de validación>");
            return {message: {state: args.args.period_name === "MISSING" ? "Sin aplicaciones asignadas" : "Conforme",
                can_assign: args.args.period_name === "MISSING"}};
        }
        if (args.method.endsWith("get_unassigned_imports")) return {message: {period: "MISSING", employer: "Empresa 2", modified: "p1", imports: [
            {name: "IMPORT", modified: "i1", count: 2, applications: ["A", "B"], amount_usd: 70,
                start_date: "2026-10-15", end_date: "2026-10-15", same_month: true}]}};
        if (args.method.endsWith("assign_and_reconcile")) return {message: {name: "MISSING", state: "Conforme", can_assign: false}};
        throw Error("Unexpected API");
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period_list.js"), "utf8"), context);
const listview = {page: {add_inner_button: (label, fn) => {actions[label] = fn;}},
    meta: {fields: []}, list_view_settings: {}, setup_columns() {}, render_header() {}, refresh() {refreshed++;}};
(async () => {
    context.frappe.listview_settings["CN Reconciliation Period"].onload(listview);
    assert.equal(typeof actions["Conciliación 1 por mes"], "function");
    actions["Conciliación 1 por mes"]();
    const main = dialogs[0];
    await main.primary_action({payroll_month: "2026-10-16"});
    assert.equal(calls[0].args.payroll_month, "2026-10-01");
    assert.deepEqual(calls.filter(c => c.method.endsWith("reconcile_period")).map(c => c.args.period_name), ["FAILED", "MISSING", "GOOD"]);
    const wrapper = main.fields_dict.results.$wrapper;
    assert.ok(wrapper.content.includes("3/3"));
    assert.ok(wrapper.content.includes("&lt;Empresa>"));
    assert.ok(wrapper.content.includes("&lt;Error de validación>"));
    assert.ok(!wrapper.content.includes("<Empresa>"));
    assert.ok(wrapper.content.includes("Asignar y reprocesar"));
    assert.equal(main.disabled, false);
    const pending = wrapper.click({preventDefault() {}, currentTarget: {dataset: {assign: "1"}}});
    await new Promise(resolve => setImmediate(resolve));
    const assign = dialogs[1];
    assert.equal(main.disabled, true);
    assert.equal(assign.fields_dict.imports.df.data[0].selected, 0);
    await assign.primary_action();
    assert.ok(messages.at(-1).includes("Seleccione"));
    assert.equal(calls.filter(c => c.method.endsWith("assign_and_reconcile")).length, 0);
    assign.fields_dict.imports.df.data[0].selected = 1;
    await assign.primary_action();
    await pending;
    const mutation = calls.find(c => c.method.endsWith("assign_and_reconcile"));
    assert.equal(mutation.args.modified, "p1");
    assert.deepEqual(JSON.parse(mutation.args.selections), [{name: "IMPORT", modified: "i1", applications: ["A", "B"]}]);
    assert.ok(!wrapper.content.includes("Sin aplicaciones asignadas"));
    assert.equal(refreshed, 2);
    actions["Conciliación 1 por mes"]();
    assert.equal(dialogs.length, 2, "reopening retains the completed report");
    writable = false;
    delete actions["Conciliación 1 por mes"];
    context.frappe.listview_settings["CN Reconciliation Period"].onload(listview);
    assert.equal(actions["Conciliación 1 por mes"], undefined);
    console.log("OK: monthly batch continues after errors, reports missing assignments, assigns explicitly and updates results.");
})().catch(error => {console.error(error); process.exitCode = 1;});
