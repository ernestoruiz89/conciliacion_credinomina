const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let events, dialog, allow = true, creditPreview = false;
const actions = {}, calls = [], properties = {};
class Dialog {
    constructor(options) {
        Object.assign(this, options);
        this.values = {};
        this.fields_dict = {summary: {$wrapper: {empty() {}, html(value) { calls.push(value); }}}};
        dialog = this;
    }
    get_value(field) { return this.values[field]; }
    async set_value(field, value) { this.values[field] = value; }
    get_primary_btn() { return {prop: (key, value) => { this[key] = value; }}; }
    show() {}
    hide() { calls.push("hide"); }
}
const summary = name => ({name, original_usd: 100, compensated_usd: 0, pending_usd: 100,
    posting_date: "2025-04-01", description: "<script>bad</script>"});
const context = vm.createContext({__: text => text, format_currency: value => `USD ${value}`, frappe: {
    ui: {form: {on: (_name, handlers) => { events = handlers; }}, Dialog},
    datetime: {get_today: () => "2026-10-01", str_to_user: date => "formatted:" + date},
    utils: {escape_html: value => value.replaceAll("<", "&lt;")},
    call: async args => {calls.push(args); return {message: {left: creditPreview
        ? {...summary("CREDIT"), is_credit: true, client_name: "Ana", client_number: "123", management_pending_usd: 0}
        : summary("A"), right: summary("B"), suggested_usd: 100, request_key: "key"}};},
    show_alert: () => calls.push("alert"), listview_settings: {},
}});
const base = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item");
vm.runInContext(fs.readFileSync(path.join(base, "cn_complementary_item.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(base, "cn_complementary_item_list.js"), "utf8"), context);
const frm = {doc: {name: "A", docstatus: 0, category: "Por clasificar"},
    is_new: () => false, is_dirty: () => true, get_perm: () => allow, trigger() {},
    set_df_property: (field, prop, value) => { properties[field + ":" + prop] = value; },
    add_custom_button: (label, handler) => {actions[label] = handler;},
    toggle_display() {},
    dashboard: {set_headline_alert() {}, clear_headline() {}},
    save: async () => calls.push("save"), reload_doc: async () => calls.push("reload")};
(async () => {
    events.refresh(frm);
    assert.equal(typeof actions["Compensar con otra partida"], "function");
    await actions["Compensar con otra partida"]();
    assert.ok(!dialog.fields.find(field => field.fieldname === "counterpart").get_query().filters.category[1].includes("Saldo a favor del cliente"));
    assert.equal(calls[0], "save");
    assert.equal(dialog.disabled, true);
    await dialog.set_value("counterpart", "B");
    await dialog.fields.find(field => field.fieldname === "counterpart").onchange();
    assert.equal(dialog.values.amount_usd, 100);
    assert.equal(dialog.disabled, false);
    const html = calls.find(value => typeof value === "string" && value.includes("&lt;script>"));
    assert.ok(html && html.includes("formatted:2025-04-01"));
    await dialog.primary_action({counterpart: "B", amount_usd: 30, compensation_date: "2025-08-01", reason: "Error", reviewed: 1});
    const confirmation = calls.find(value => value.method?.endsWith("confirm_compensation"));
    assert.equal(confirmation.args.request_key, "key");
    assert.equal(confirmation.args.amount_usd, 30);
    assert.ok(calls.includes("reload"));
    frm.doc = {name: "A", docstatus: 1, category: "Compensación entre partidas", compensations: [{}], compensation_pending_usd: 70};
    events.refresh(frm);
    assert.equal(properties["amount:read_only"], 1);
    assert.equal(typeof actions["Consultar saldo a fecha"], "function");
    delete actions["Compensar con otra partida"];
    frm.doc.compensation_pending_usd = 0;
    events.refresh(frm);
    assert.equal(actions["Compensar con otra partida"], undefined);
    frm.doc.compensation_pending_usd = 70;
    allow = false;
    events.refresh(frm);
    assert.equal(actions["Compensar con otra partida"], undefined);
    const indicator = context.frappe.listview_settings["CN Complementary Item"].get_indicator({category: frm.doc.category, compensation_status: "Compensada totalmente"});
    assert.equal(indicator[1], "green");
    allow = true;
    frm.doc = {name: "CREDIT", docstatus: 1, category: "Saldo a favor del cliente", employer: "EMP",
        registered_deposit: "DEP", result: "Saldo a favor documentado", credit_pending_usd: 0};
    events.refresh(frm);
    assert.equal(typeof actions["Compensar con otra partida"], "function");
    await actions["Compensar con otra partida"]();
    const filters = dialog.fields.find(field => field.fieldname === "counterpart").get_query().filters;
    assert.equal(filters.employer, "EMP");
    assert.equal(filters.source_debit[1], 0);
    assert.equal(filters.source_credit, 0);
    assert.equal(filters.accounting_source_key[1], "set");
    assert.ok(filters.category[1].includes("Saldo a favor del cliente"));
    assert.ok(dialog.fields.find(field => field.fieldname === "instructions").options.includes("no cambia la distribución del depósito ni el pendiente de devolución"));
    creditPreview = true;
    await dialog.set_value("counterpart", "B");
    await dialog.fields.find(field => field.fieldname === "counterpart").onchange();
    assert.ok(calls.some(value => typeof value === "string" && value.includes("Sin vínculo contable US$")
        && value.includes("Pendiente de gestión US$ (no cambia)") && value.includes("Nro. Cliente: 123")));
    delete actions["Compensar con otra partida"];
    frm.doc.compensations = [{}]; frm.doc.compensation_pending_usd = 0;
    events.refresh(frm);
    assert.equal(actions["Compensar con otra partida"], undefined);
    assert.equal(typeof actions["Revertir compensación"], "function");
    frm.doc.docstatus = 0;
    events.refresh(frm);
    assert.equal(actions["Compensar con otra partida"], undefined);
    console.log("OK: selección, confirmación, permisos, historial bloqueado, fechas y escape HTML.");
})().catch(error => {console.error(error); process.exitCode = 1;});
