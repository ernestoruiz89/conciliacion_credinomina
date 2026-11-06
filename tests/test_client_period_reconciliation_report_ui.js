const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const context = vm.createContext({__: text => text, frappe: {query_reports: {}, utils: {
    escape_html: text => text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
}}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/report/conciliacion_por_cliente_y_periodo/conciliacion_por_cliente_y_periodo.js", "utf8"), context);
const report = context.frappe.query_reports["Conciliacion por Cliente y Periodo"];
assert.equal(report.filters.find(field => field.fieldname === "period").reqd, 1);
const render = (field, value, data = {}) => report.formatter(value, 0, {fieldname: field}, {[field]: value, ...data}, String);
assert.equal(render("comment", ""), "");
assert.ok(render("comment", "<script>\nDato documentado").includes("&lt;script&gt;"));
assert.equal(render("applied_usd", 0), "—");
assert.ok(render("pending_usd", null).includes("No determinable"));
assert.ok(render("pending_usd", 7.05).includes("text-danger"));
assert.ok(render("state", "Revisar").includes("text-danger"));
assert.equal(report.get_datatable_options({}).serialNoColumn, false);
assert.equal(report.get_datatable_options({}).dynamicRowHeight, true);
const handlers = {}, actions = {}, routes = [];
context.frappe.ui = {form: {on: (doctype, events) => {handlers[doctype] = events;}}};
context.frappe.session = {user: 'reader@example.test'};
context.frappe.user = {has_role: () => false};
context.frappe.set_route = (...args) => routes.push(args);
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js', 'utf8'), context);
for (const name of ['renderPeriodFinancialOverview', 'setPeriodEditing', 'refreshLegacyEmployerDetail',
    'setRemittanceDateEditing', 'refreshPeriodExceptions', 'refreshPeriodPending', 'refreshApplicationQuality']) context[name] = () => {};
handlers['CN Reconciliation Period'].refresh({doc: {name: 'P', status: 'Cerrado', reconciliation_mode: 'Historica'},
    fields_dict: {}, is_new: () => false, add_custom_button: (label, callback) => {actions[label] = callback;}});
actions['Conciliación por cliente y período']();
assert.equal(routes[0][0], 'query-report');
assert.equal(routes[0][1], 'Conciliacion por Cliente y Periodo');
assert.equal(routes[0][2].period, 'P');
console.log("Client/period reconciliation report UI: passed");
