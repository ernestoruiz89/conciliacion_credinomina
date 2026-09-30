const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const events = [];
let dialogOptions;
const formHandlers = {};
const result = {employer: "A", imports: 1, rows: 1, matched: 0, pending: 1, ignored: 0,
    pending_rows: [{import_name: "IA", row: 2, client_name: "<script>bad</script>", loan_number: "101", reason: "Falta depósito"}]};
const context = vm.createContext({
    __: (text, values = []) => text.replace(/\{(\d+)\}/g, (_, i) => values[i]),
    frappe: {
        ui: {form: {on(doctype, handlers) {formHandlers[doctype] = handlers;}}, Dialog: function(options) {
            dialogOptions = options;
            this.show = () => events.push("dialog");
            this.hide = () => {};
        }},
        utils: {escape_html: value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        call: async args => {events.push("call"); assert.equal(args.args.import_name, "IA"); return {message: result};},
        msgprint: () => events.push("message"),
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_source_import/cn_source_import.js"), "utf8"), context);

(async () => {
    const activated = [];
    const rowForm = {fields_dict: {rows: {grid: {grid_rows_by_docname: {
        A: {grid_form: {layout: {tabs: [
            {df: {fieldname: "reconciliation_tab"}, set_active: () => activated.push("wrong-tab")},
            {df: {fieldname: "movement_tab"}, set_active: () => activated.push("A-movement")},
        ]}}},
        B: {grid_form: {layout: {tabs: [
            {df: {fieldname: "movement_tab"}, set_active: () => activated.push("B-movement")},
        ]}}},
    }}}}};
    const renderRow = cdn => formHandlers["CN Source Row"].form_render(rowForm, "CN Source Row", cdn);
    renderRow("A");
    renderRow("B");
    renderRow("A"); // Reopening a row must also default to Movimiento.
    renderRow("missing"); // A removed/unrendered row must not throw.
    assert.deepEqual(activated, ["A-movement", "B-movement", "A-movement"]);

    const frm = {doc: {employer: "A", name: "IA"}, is_dirty: () => true,
        save: async () => events.push("save"), reload_doc: async () => events.push("reload")};
    await context.reconcileCompany(frm);
    assert.deepEqual(events, ["save", "call", "reload", "dialog"]);
    const html = dialogOptions.fields[0].options;
    assert.ok(html.includes("Falta depósito"));
    assert.ok(html.includes("&lt;script&gt;bad&lt;/script&gt;"));
    assert.ok(!html.includes("<script>"));
    assert.equal(frm.__company_reconciliation_running, false);

    context.showCompanyReconciliation({...result, rows: 3, matched: 2,
        matched_rows: [{import_name: "IB", row: 3, client_name: "<b>Cliente conciliado</b>",
            loan_number: "102", reason: "Aplicación y depósito conciliados."}]});
    const mixedHtml = dialogOptions.fields[0].options;
    assert.equal((mixedHtml.match(/<tr class="cn-row-reconciled">/g) || []).length, 1);
    assert.ok(mixedHtml.includes("background-color: var(--bg-green"));
    assert.ok(mixedHtml.includes("<td>Conciliado</td>"));
    assert.ok(mixedHtml.includes("<td>Pendiente</td>"));
    assert.ok(mixedHtml.includes("Se muestran 1 de 2 filas conciliadas"));
    assert.ok(mixedHtml.includes("&lt;b&gt;Cliente conciliado&lt;/b&gt;"));
    assert.ok(!mixedHtml.includes("<b>Cliente conciliado</b>"));
    context.showCompanyReconciliation({...result, pending: 0, pending_rows: [], matched: 1,
        matched_rows: [{import_name: "IB", row: 3, client_name: "Ana", reason: "Conciliado"}]});
    assert.ok(dialogOptions.fields[0].options.includes('class="cn-row-reconciled"'));

    events.length = 0;
    frm.save = async () => {throw new Error("save failed");};
    await assert.rejects(context.reconcileCompany(frm), /save failed/);
    assert.deepEqual(events, []);
    assert.equal(frm.__company_reconciliation_running, false);

    events.length = 0;
    frm.save = async () => events.push("save");
    context.frappe.call = async () => {throw new Error("reconciliation failed");};
    await assert.rejects(context.reconcileCompany(frm), /reconciliation failed/);
    assert.deepEqual(events, ["save"]);
    assert.equal(frm.__company_reconciliation_running, false);

    events.length = 0;
    frm.doc.employer = "";
    await context.reconcileCompany(frm);
    assert.deepEqual(events, ["message"]);
    console.log("Company reconciliation UI checks passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
