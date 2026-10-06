const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
let events;
const context = vm.createContext({
    __: (value, args = []) => value.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    frappe: {ui: {form: {on: (_, handlers) => { events = handlers; }}},
        session: {user: "Administrator"}, user: {has_role: () => false}, utils: {escape_html: value => value.replace(/</g, "&lt;")}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
const fields = ["employer", "payroll_month", "application_basis", "remark", "collection_file", "collection_rows", "remittance_due_date", "applied_usd"]
    .map(fieldname => ({df: {fieldname, read_only: fieldname === "applied_usd" ? 1 : 0}}));
const frm = {fields, doc: {status: "Cerrado", reconciliation_mode: "Operativa", collection_cycle: "Primera quincena"},
    buttons: [], is_new: () => false, get_perm: () => true,
    set_df_property(name, property, value) { this.fields.find(field => field.df.fieldname === name).df[property] = value; },
    disable_save() { this.disabled = true; }, enable_save() { this.disabled = false; },
    set_intro(value) { this.intro = value; }, add_custom_button(label) { this.buttons.push(label); }};
events.refresh(frm);
assert.ok(frm.intro.includes("Sin resultado registrado"));
frm.doc.status_before_close = "Conciliado";
events.refresh(frm);
assert.ok(frm.intro.includes("Resultado al cerrar: Conciliado"));
assert.ok(fields.every(field => !!field.df.read_only));
assert.equal(frm.disabled, true);
assert.ok(frm.buttons.includes("Reabrir período"));
assert.ok(!frm.buttons.includes("1. Cargar cobranza"));
events.collection_cycle(frm);
assert.equal(fields.find(field => field.df.fieldname === "remittance_due_date").df.read_only, true);
events.refresh(frm); // Repeated refresh must not replace the original field settings.
frm.doc.status = "Conciliado";
frm.buttons = [];
events.refresh(frm);
assert.equal(frm.disabled, false);
assert.equal(frm.intro, "");
assert.equal(fields.find(field => field.df.fieldname === "remark").df.read_only, 0);
assert.equal(fields.find(field => field.df.fieldname === "collection_rows").df.read_only, 0);
assert.equal(fields.find(field => field.df.fieldname === "applied_usd").df.read_only, 1);
assert.equal(fields.find(field => field.df.fieldname === "remittance_due_date").df.read_only, false);
assert.ok(frm.buttons.includes("1. Cargar cobranza"));
// No reopen button for an ordinary operator, including historical periods.
context.frappe.session.user = "operator@example.com";
frm.doc = {status: "Cerrado", reconciliation_mode: "Historica"};
frm.buttons = [];
events.refresh(frm);
assert.equal(frm.disabled, true);
assert.ok(!frm.buttons.includes("Reabrir período"));
assert.ok(!frm.buttons.includes("Cerrar período histórico"));
// Navigating to an open record with no write permission must not enable save.
frm.get_perm = () => false;
frm.doc.status = "Pendiente";
events.refresh(frm);
assert.equal(frm.disabled, true);
console.log("OK: closed period fields/table, due date, reopening, navigation and operator permissions.");
