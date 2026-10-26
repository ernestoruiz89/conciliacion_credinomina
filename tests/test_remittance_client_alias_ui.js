const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const handlers = {}, calls = [];
let dialog, formatter, dirty = true, saved = 0, reloaded = 0, allowClient = true, click;
const context = vm.createContext({__: text => text, frappe: {
    model: {can_write: () => allowClient},
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    meta: {get_docfield: () => ({})}, msgprint() {}, show_alert() {},
    ui: {form: {on: (dt, events) => {handlers[dt] = events;}}, Dialog: function(options) {
        Object.assign(this, options); dialog = this;
        this.show = () => {};
        this.hide = () => this.onhide?.();
        this.disable_primary_action = () => {};
        this.enable_primary_action = () => {};
    }},
    call: async args => {
        calls.push(args);
        return {message: args.method.endsWith("preview_alias") ? {client: "2635", client_name: "JEFFRY RAMON GARCIA SANCHEZ",
            alias_name: "JEFRI RAMON GARCIA SANCHEZ", modified: "d1", client_modified: "c1", loan_number: "12671-1"} : {client: "2635"}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const row = {name: "R66", parent: "DEP", client_name: "JEFRI RAMON GARCIA SANCHEZ", loan_number: "12671-1",
    identity_reason: "Conflicto: no se pudo verificar Nombre y apellidos en los clientes de la empresa"};
const frm = {doc: {name: "DEP", docstatus: 1, detail_rows: [row]}, is_new: () => false, get_perm: () => true,
    is_dirty: () => dirty, save: async () => {saved++; dirty = false;}, reload_doc: async () => {reloaded++;},
    fields_dict: {detail_rows: {grid: {update_docfield_property: (_field, _property, value) => {formatter = value;}},
        $wrapper: {off() {return this;}, on(_event, _selector, fn) {click = fn;}}}}};
(async () => {
    assert.equal(context.canCreateRemittanceClientAlias(frm, row), true);
    for (const changes of [{client: "2635"}, {identity_reason: "Conflicto: no se pudo verificar Nro. Cliente"},
        {identity_reason: "Nombre ambiguo entre varios clientes"}, {loan_number: ""}, {client_name: ""}]) {
        assert.equal(context.canCreateRemittanceClientAlias(frm, {...row, ...changes}), false);
    }
    allowClient = false;
    assert.equal(context.canCreateRemittanceClientAlias(frm, row), false);
    allowClient = true;
    context.setupRemittanceClientAlias(frm);
    assert.ok(formatter(row.client_name, {}, {}, row).includes("Crear alias"));
    assert.ok(!formatter("<Persona>", {}, {}, {...row, client: "2635"}).includes("Crear alias"));
    assert.ok(formatter("<Persona>", {}, {}, row).includes("&lt;Persona>"));
    assert.ok(!formatter(row.client_name, {}, {}, {...row, parent: "OTHER"}).includes("Crear alias"));
    let propagationStopped = false;
    click({preventDefault() {}, stopPropagation() {propagationStopped = true;}, currentTarget: {dataset: {createClientAlias: "R66"}}});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(propagationStopped, true);
    assert.equal(saved, 1);
    assert.equal(calls[0].args.detail_row_name, "R66");
    assert.equal(dialog.fields.find(f => f.fieldname === "alias_name").read_only, 1);
    const query = dialog.fields.find(f => f.fieldname === "client").get_query();
    assert.deepEqual(Array.from(query.filters.name[1]), ["2635"]);
    await dialog.primary_action({client: "2635"});
    assert.equal(calls[1].args.client_modified, "c1");
    assert.equal(calls[1].args.modified, "d1");
    assert.equal(calls[1].args.client_name, "2635");
    assert.equal(reloaded, 1);
    assert.equal(frm.clientAliasDialog, null);
    frm.doc.docstatus = 2;
    assert.equal(context.canCreateRemittanceClientAlias(frm, row), false);
    console.log("OK: name-only conflict action, verified client filter, source alias, saved form and reload.");
})().catch(error => {console.error(error); process.exitCode = 1;});
