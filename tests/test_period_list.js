const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const directory = path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period");
const meta = JSON.parse(fs.readFileSync(path.join(directory, "cn_reconciliation_period.json"), "utf8"));
const status = meta.fields.find(field => field.fieldname === "status");
assert.equal(status.in_list_view, 1);
const states = Object.fromEntries(meta.states.map(state => [state.title, state.color]));
assert.deepEqual(Object.keys(states).sort(), status.options.split("\n").sort());
assert.equal(meta.states.length, Object.keys(states).length);
assert.equal(states.Borrador, "Gray");
for (const title of ["Pendiente", "Parcial"]) assert.equal(states[title], "Orange");
assert.equal(states["Con excedente"], "Red");
assert.equal(states.Conciliado, "Green");
assert.equal(states.Cerrado, "Purple");
const context = vm.createContext({__: value => value, frappe: {listview_settings: {},
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
console.log("OK: status column, complete color mapping and status fetch for personalized lists.");
