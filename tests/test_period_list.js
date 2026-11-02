const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const directory = path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period");
const meta = JSON.parse(fs.readFileSync(path.join(directory, "cn_reconciliation_period.json"), "utf8"));
const status = meta.fields.find(field => field.fieldname === "status");
assert.equal(status.in_list_view, 1);
assert.equal(status.in_standard_filter, 1);
const employer = meta.fields.find(field => field.fieldname === "employer");
assert.equal(employer.in_standard_filter, 1);
assert.equal(employer.fieldtype, "Link");
assert.equal(employer.options, "CN Employer");
const states = Object.fromEntries(meta.states.map(state => [state.title, state.color]));
assert.deepEqual(Object.keys(states).sort(), status.options.split("\n").sort());
assert.equal(meta.states.length, Object.keys(states).length);
assert.equal(states.Borrador, "Gray");
for (const title of ["Pendiente", "Parcial"]) assert.equal(states[title], "Orange");
assert.equal(states["Con excedente"], "Red");
assert.equal(states.Conciliado, "Green");
assert.equal(states.Cerrado, "Purple");
const context = vm.createContext({__: value => value, format_currency: (value, currency) => `${currency} ${Number(value).toFixed(2)}`, frappe: {listview_settings: {},
    model: {can_write: () => false},
    format: value => `USD ${Number(value).toFixed(2)}`, ui: {form: {on() {}}},
    get_meta: () => meta, scrub: value => value.toLowerCase(),
    utils: {escape_html: value => value.replace(/</g, "&lt;").replace(/>/g, "&gt;")}}});
vm.runInContext(fs.readFileSync(path.join(directory, "cn_reconciliation_period_list.js"), "utf8"), context);
assert.ok(context.frappe.listview_settings[meta.name].add_fields.includes("status"));
const settings = context.frappe.listview_settings[meta.name];
assert.ok(settings.add_fields.includes("status_before_close"));
const formatter = settings.formatters.status_before_close;
assert.ok(formatter("Conciliado", {}, {status: "Cerrado"}).includes("green"));
assert.ok(formatter("Con excedente", {}, {status: "Cerrado"}).includes("red"));
assert.ok(formatter("Parcial", {}, {status: "Cerrado"}).includes("orange"));
assert.ok(formatter(null, {}, {status: "Cerrado"}).includes("Sin resultado registrado"));
assert.equal(formatter("Conciliado", {}, {status: "Pendiente"}), "—");
assert.ok(!formatter("<script>", {}, {status: "Cerrado"}).includes("<script>"));
assert.equal(meta.fields.find(field => field.fieldname === "status_before_close").in_list_view, 1);
assert.equal(meta.field_order.filter(field => field === "status_before_close").length, 1);
const view = {meta, list_view_settings: {fields: '[{"fieldname":"employer"}]'},
    setup_columns() {}, render_header() {}};
settings.onload(view);
settings.onload(view);
assert.equal(JSON.parse(view.list_view_settings.fields).filter(field => field.fieldname === "status_before_close").length, 1);
const hiddenColumns = ["reconciliation_mode", "historical_application_date", "historical_start_date", "historical_end_date"];
for (const fieldname of hiddenColumns) {
    assert.equal(meta.fields.find(field => field.fieldname === fieldname).in_list_view, 0);
    assert.ok(meta.field_order.includes(fieldname));
}
view.list_view_settings.fields = JSON.stringify([
    {fieldname: "employer"}, {fieldname: "status"}, {fieldname: "historical_scope"},
    ...hiddenColumns.map(fieldname => ({fieldname})),
]);
settings.onload(view);
const displayed = JSON.parse(view.list_view_settings.fields).map(field => field.fieldname);
assert.ok(hiddenColumns.every(fieldname => !displayed.includes(fieldname)));
assert.ok(["employer", "status", "historical_scope", "status_before_close"].every(fieldname => displayed.includes(fieldname)));
view.list_view_settings.fields = null;
settings.onload(view);
for (const fieldname of ["deposit_coverage_usd", "complementary_coverage_usd", "negative_complementary_usd"]) {
    assert.ok(settings.add_fields.includes(fieldname));
    assert.equal(settings.formatters[fieldname](90, {}, {cash_breakdown_known: 0}), "—");
    assert.equal(meta.fields.find(field => field.fieldname === fieldname).depends_on, "eval:doc.cash_breakdown_known == 1");
}
assert.equal(settings.formatters.deposit_coverage_usd(90, {}, {cash_breakdown_known: 1}), "USD 90.00");
assert.equal(settings.formatters.negative_complementary_usd(-10, {}, {cash_breakdown_known: 1}), "USD -10.00");
vm.runInContext(fs.readFileSync(path.join(directory, "cn_reconciliation_period.js"), "utf8"), context);
let summary = "";
const form = {is_new: () => false, fields_dict: {financial_overview_html: {$wrapper: {html: value => {summary = value;}}}},
    doc: {applied_total_usd: 100, deposit_coverage_usd: 90, complementary_coverage_usd: 10,
        negative_complementary_usd: -10, pending_usd: 0, cash_breakdown_known: 1}};
context.renderPeriodFinancialOverview(form);
assert.ok(summary.includes("USD 90.00") && summary.includes("USD -10.00"));
assert.ok(summary.includes("Complementarias negativas US$ (incluidas)") && summary.includes("no se suma otra vez"));
form.doc.cash_breakdown_known = 0;
form.doc.cash_breakdown_note = "<script>sin reparto</script>";
context.renderPeriodFinancialOverview(form);
assert.ok(summary.includes("—"));
assert.ok(!summary.includes("USD 90.00") && !summary.includes("USD -10.00"));
assert.ok(!summary.includes("<script>"));
console.log("OK: status column, complete color mapping and status fetch for personalized lists.");
