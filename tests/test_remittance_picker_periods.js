// Run with: node tests/test_remittance_picker_periods.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function wrapper() {
    return {
        content: "", handlers: new Map(), attrs: {},
        attr(values) { Object.assign(this.attrs, values); return this; },
        html(value) { this.content = value; return this; },
        on(event, selector, handler) { this.handlers.set(`${event}:${selector}`, handler); return this; },
        val() { return this.value || ""; },
        find() { return this; }, prop() { return this; }, is() { return true; },
    };
}
class Dialog {
    constructor(options) {
        this.options = options;
        this.fields_dict = {};
        this.values = {};
        this.$wrapper = wrapper();
        this.minimizeButton = wrapper();
        for (const field of options.fields) {
            if (!field.fieldname) continue;
            this.fields_dict[field.fieldname] = { df: field, $input: wrapper(), $wrapper: wrapper(),
                set_input: value => { this.values[field.fieldname] = value; } };
            this.values[field.fieldname] = field.fieldtype === "Check" ? 0 : field.default || "";
            if (field.fieldtype === "Check") {
                // Reproduce Frappe's async defaults while Bootstrap is hidden:
                // onchange cannot be relied upon to redraw the initial table.
                Promise.resolve().then(() => {
                    this.fields_dict[field.fieldname].set_input(field.default || 0);
                    this.$wrapper.is = () => false;
                    field.onchange?.();
                    this.$wrapper.is = () => true;
                });
            }
        }
    }
    get_value(name) { return this.values[name]; }
    set_value(name, value) {
        this.values[name] = value;
        this.fields_dict[name].df.onchange?.();
    }
    get_primary_btn() { return wrapper(); }
    get_minimize_btn() { return this.minimizeButton; }
    show() {}
}
const context = {
    __: value => value,
    frappe: { ui: { form: { on() {} }, Dialog }, utils: { escape_html: value => value } },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const Picker = vm.runInContext("RemittanceTargetPicker", context);
const rows = [
    { id: "H:A", filter_period: "A", kind: "Aplicación histórica", employer: "Empresa A" },
    { id: "H:B", filter_period: "B", kind: "Aplicación histórica", employer: "Empresa B" },
    { id: "H:C", filter_period: "C", kind: "Aplicación histórica", employer: "Empresa A" },
    { id: "C:A", filter_period: "A", kind: "Cobranza", employer: "Empresa A" },
    { id: "X:B", filter_period: "B", kind: "Partida complementaria", employer: "Empresa B" },
    { id: "X:NONE", filter_period: "", kind: "Partida complementaria", employer: "Empresa A" },
].map(row => ({...row, pending_cents: 100, client_name: "Ana Pérez", reference: "REF"}));
const data = { rows, employer: "Empresa A", allowed_employers: ["Empresa A", "Empresa B"], available_cents: 10000 };
async function run() {
const picker = new Picker({doc: {detail_periods: [{period: "A"}, {period: "B"}, {period: "A"}, {}]}}, data);
const ids = () => Array.from(picker.filtered, row => row.id);
assert.equal(picker.dialog.get_value("use_detail_periods"), 1);
assert.equal(picker.detailPeriods.size, 2);
assert.deepEqual(ids(), ["H:A", "H:B", "C:A", "X:B"]);
// Already filtered on the first render, before delayed defaults have run.
const initialItems = picker.dialog.fields_dict.items.$wrapper.content;
await Promise.resolve();
assert.deepEqual(ids(), ["H:A", "H:B", "C:A", "X:B"]);
assert.equal(picker.dialog.fields_dict.items.$wrapper.content, initialItems);
const query = picker.dialog.fields_dict.period.df.get_query;
assert.deepEqual(Array.from(query().filters.name[1]), ["A", "B"]);
assert.equal(query().filters.status[1], "Cerrado");

// All pages selected by the existing action stay limited to the chosen periods.
picker.dialog.fields_dict.items.$wrapper.handlers.get("click:[data-action]")({currentTarget: {dataset: {action: "select"}}});
assert.deepEqual(Array.from(picker.selected.keys()), ["H:A", "H:B", "C:A", "X:B"]);
// Use Frappe's native minimization: the picker and its fields are not recreated.
assert.equal(picker.dialog.options.minimizable, true);
assert.equal(picker.dialog.minimizeButton.attrs["aria-label"], "Minimizar selector de partidas");
const selectedBefore = Array.from(picker.selected.entries());
const itemsBefore = picker.dialog.fields_dict.items.$wrapper.content;
picker.dialog.options.on_minimize_toggle.call(picker.dialog, true);
assert.equal(picker.dialog.minimizeButton.attrs.title, "Restaurar selector de partidas");
picker.dialog.options.on_minimize_toggle.call(picker.dialog, false);
assert.equal(picker.dialog.minimizeButton.attrs["aria-label"], "Minimizar selector de partidas");
assert.deepEqual(Array.from(picker.selected.entries()), selectedBefore);
assert.equal(picker.dialog.fields_dict.items.$wrapper.content, itemsBefore);
assert.equal(picker.dialog.get_value("use_detail_periods"), 1);
picker.selected.clear();

// The multi-period check intersects the existing type, company, search and single-period filters.
picker.dialog.set_value("kind", "Partida complementaria");
assert.deepEqual(ids(), ["X:B"]);
picker.dialog.set_value("beneficiary", "Empresa A");
assert.deepEqual(ids(), []);
picker.dialog.set_value("kind", "Todos");
assert.deepEqual(ids(), ["H:A", "C:A"]);
picker.dialog.fields_dict.search.$input.value = "ana perez";
picker.filter();
assert.deepEqual(ids(), ["H:A", "C:A"]);
picker.dialog.set_value("beneficiary", "");
picker.dialog.set_value("period", "B");
assert.deepEqual(ids(), ["H:B", "X:B"]);

picker.dialog.set_value("use_detail_periods", 0);
picker.dialog.set_value("period", "");
assert.deepEqual(ids(), rows.map(row => row.id));
assert.equal(query().filters.name, undefined);
picker.dialog.set_value("period", "C");
picker.page = 4;
picker.dialog.set_value("use_detail_periods", 1);
assert.equal(picker.dialog.get_value("period"), "");
assert.equal(picker.page, 0);
assert.deepEqual(ids(), ["H:A", "H:B", "C:A", "X:B"]);

const empty = new Picker({doc: {}}, data);
assert.equal(Number(empty.dialog.get_value("use_detail_periods")), 0);
assert.equal(empty.dialog.fields_dict.use_detail_periods.df.read_only, 1);
assert.deepEqual(Array.from(empty.filtered, row => row.id), rows.map(row => row.id));
assert.match(empty.dialog.fields_dict.use_detail_periods.df.description, /Agregue períodos/);
await Promise.resolve();
assert.deepEqual(Array.from(empty.filtered, row => row.id), rows.map(row => row.id));
console.log("OK: detail-period checkbox, defaults, multiple companies and kinds, intersecting filters, selection and empty table.");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
