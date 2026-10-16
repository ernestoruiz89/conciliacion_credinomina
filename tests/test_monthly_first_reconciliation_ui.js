const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const dialogs = [], calls = [], messages = [], actions = {};
let writable = true, refreshed = 0;
let stableScroller, stableBody;
class Element {
    constructor() {this.children = new Map(); this.htmlWrites = 0; this.scrollTop = 0;}
    find(selector) {
        if (!this.children.has(selector)) this.children.set(selector, new Element());
        return this.children.get(selector);
    }
    html(value) {this.content = value; this.htmlWrites++; this.children.clear(); return this;}
    text(value) {this.textValue = String(value); return this;}
    toggle(value) {this.visible = value; return this;}
    css(values) {this.styles = values; return this;}
    on(_event, _selector, fn) {this.click = fn;}
}
const context = vm.createContext({__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]), frappe: {
    listview_settings: {}, model: {can_write: () => writable}, datetime: {now_date: () => "2026-10-16"},
    utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    show_alert() {}, msgprint: text => messages.push(text),
    ui: {Dialog: function(options) {
        Object.assign(this, options);
        this.$wrapper = new Element();
        this.fields_dict = Object.fromEntries(options.fields.filter(f => f.fieldname).map(df => [df.fieldname,
            {df, $wrapper: new Element()}]));
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
            const wrapper = dialogs[0].fields_dict.results.$wrapper;
            const scroller = wrapper.find("[data-results-scroll]");
            const body = wrapper.find("[data-results-body]");
            if (!stableScroller) {stableScroller = scroller; stableBody = body; scroller.scrollTop = 280;}
            assert.equal(scroller, stableScroller, "progress must keep the scroll container");
            assert.equal(body, stableBody, "progress must keep the table body");
            assert.equal(scroller.scrollTop, 280);
            assert.equal(wrapper.htmlWrites, 1, "progress must not rebuild the table");
            assert.equal(body.htmlWrites, 2, "rows are populated once after clearing the previous month");
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
    assert.equal(typeof actions["Conciliación Cobranza-Aplicacion"], "function");
    actions["Conciliación Cobranza-Aplicacion"]();
    const main = dialogs[0];
    assert.equal(main.title, "Conciliación Cobranza-Aplicacion");
    assert.equal(main.$wrapper.find(".modal-dialog").styles.width, "96vw");
    await main.primary_action({payroll_month: "2026-10-16"});
    assert.equal(calls[0].args.payroll_month, "2026-10-01");
    assert.deepEqual(calls.filter(c => c.method.endsWith("reconcile_period")).map(c => c.args.period_name), ["FAILED", "MISSING", "GOOD"]);
    const wrapper = main.fields_dict.results.$wrapper;
    assert.ok(wrapper.find("[data-progress]").textValue.includes("3/3"));
    assert.ok(stableBody.content.includes("&lt;Empresa>"));
    const errorCell = wrapper.find('[data-result-row="0"]').find("[data-detail]");
    assert.equal(errorCell.textValue, "<Error de validación>");
    assert.equal(errorCell.htmlWrites, 0, "error messages must be inserted as text");
    assert.ok(!stableBody.content.includes("<Empresa>"));
    assert.equal(wrapper.find('[data-assign="1"]').visible, true);
    assert.equal(stableScroller.scrollTop, 280);
    stableScroller.scrollTop = 410;
    assert.equal(main.disabled, false);
    const pending = wrapper.click({preventDefault() {}, currentTarget: {dataset: {assign: "1"}}});
    await new Promise(resolve => setImmediate(resolve));
    const assign = dialogs[1];
    assert.equal(assign.$wrapper.find(".modal-dialog").styles.width, "96vw");
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
    assert.ok(!wrapper.find("[data-summary]").textValue.includes("Sin aplicaciones asignadas"));
    assert.equal(wrapper.find('[data-result-row="1"]').find("[data-state]").textValue, "Conforme");
    assert.equal(wrapper.find('[data-assign="1"]').visible, false);
    assert.equal(stableScroller.scrollTop, 410);
    assert.equal(wrapper.htmlWrites, 1);
    assert.equal(stableBody.htmlWrites, 2, "reprocessing must not rebuild rows");
    assert.equal(refreshed, 2);
    actions["Conciliación Cobranza-Aplicacion"]();
    assert.equal(dialogs.length, 2, "reopening retains the completed report");
    writable = false;
    delete actions["Conciliación Cobranza-Aplicacion"];
    context.frappe.listview_settings["CN Reconciliation Period"].onload(listview);
    assert.equal(actions["Conciliación Cobranza-Aplicacion"], undefined);
    console.log("OK: renamed wide dialogs, stable table and scroll during progress and reprocessing, explicit assignment and error recovery.");
})().catch(error => {console.error(error); process.exitCode = 1;});
