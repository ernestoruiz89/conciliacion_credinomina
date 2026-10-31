const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const handlers = {}, calls = [];
let registrations = 0, dirty = false;
const context = vm.createContext({__: text => text, format_currency: value => Number(value).toFixed(2), frappe: {
    utils: {escape_html: value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    call: args => new Promise(resolve => calls.push({args, resolve})),
    ui: {form: {on(dt, events) {registrations++; handlers[dt] = events;}}},
}});
const script = fs.readFileSync("credinomina_reconciliation/public/js/row_link_details.js", "utf8");
vm.runInContext(script, context); vm.runInContext(script, context);
assert.equal(registrations, 2, "Shared script must not register duplicate events across parent forms");
for (const type of ["cn_remittance_detail", "cn_source_row"]) {
    const meta = JSON.parse(fs.readFileSync(`credinomina_reconciliation/conciliacion_credinomina/doctype/${type}/${type}.json`, "utf8"));
    assert.equal(meta.fields.find(field => field.fieldname === "linked_documents_tab").fieldtype, "Tab Break");
    assert.equal(meta.fields.find(field => field.fieldname === "linked_documents_html").fieldtype, "HTML");
    assert.ok(meta.field_order.includes("linked_documents_html"));
    assert.equal(meta.fields.find(field => field.fieldname === "linked_summary_html").fieldtype, "HTML");
    assert.ok(meta.field_order.indexOf("linked_summary_html") < meta.field_order.indexOf("linked_documents_html"));
}
function form(field, doctype) {
    const wrapper = {html(value) {this.value = value;}, off() {}, on(_event, _selector, fn) {this.refresh = fn;}};
    const summary = {html(value) {this.value = value;}};
    const row = {name: "ROW"}, control = {$wrapper: wrapper};
    const gridRow = {grid_form: {fields_dict: {linked_documents_html: control, linked_summary_html: {$wrapper: summary}}}};
    const frm = {doc: {doctype, name: "PARENT", modified: "V1", [field]: [row]}, is_new: () => false, is_dirty: () => dirty,
        fields_dict: {[field]: {grid: {grid_rows_by_docname: {ROW: gridRow}}}}};
    return {frm, wrapper, row, summary};
}
(async () => {
    for (const [child, field, parent] of [["CN Remittance Detail", "detail_rows", "CN Remittance Allocation"],
                                       ["CN Source Row", "rows", "CN Accounting Import"]]) {
        const {frm, wrapper, summary} = form(field, parent);
        const pending = handlers[child].form_render(frm, child, "ROW");
        const request = calls.at(-1);
        assert.deepEqual(JSON.parse(JSON.stringify(request.args.args)), {doctype: parent, name: "PARENT", row_name: "ROW", modified: "V1"});
        request.resolve({message: {modified: "V1", rows: [{doctype: "CN Accounting Import", document: "IMP/<unsafe>",
            row_label: "Fila 10", kind: "Aplicación", state: "Conciliada", amount_usd: 19.25}], total_usd: 19.25, notes: [],
            summary: [{label: "Importe de la fila US$", amount_usd: 20}, {label: "Vinculado a créditos US$", amount_usd: 19.25},
                {label: "Partidas complementarias US$", amount_usd: 0}, {label: "Sin vincular US$", amount_usd: .75}]}});
        await pending;
        assert.ok(wrapper.value.includes("Total vinculado US$"));
        assert.ok(wrapper.value.includes("19.25"));
        assert.ok(wrapper.value.includes("/app/cn-accounting-import/IMP%2F%3Cunsafe%3E"));
        assert.ok(wrapper.value.includes("&lt;unsafe>"));
        assert.ok(!wrapper.value.includes("<unsafe>"));
        assert.ok(summary.value.includes("data-row-link-summary"));
        assert.equal((summary.value.match(/font-weight:600/g) || []).length, 4);
        assert.ok(summary.value.includes("20.00"));
        assert.ok(summary.value.includes("0.75"));
        assert.ok(!wrapper.value.includes("data-row-link-summary"), "Cards belong above the document HTML field");
        dirty = true;
        const before = calls.length;
        await handlers[child].form_render(frm, child, "ROW");
        assert.equal(calls.length, before, "Unsaved edits must not be presented as stored links");
        assert.ok(wrapper.value.includes("Guarde o recargue")); dirty = false;
        assert.equal(summary.value, "", "Do not retain old cards while the row has unsaved edits");
        const obsolete = handlers[child].form_render(frm, child, "ROW");
        const current = handlers[child].form_render(frm, child, "ROW");
        calls.at(-1).resolve({message: {modified: "V1", rows: [], total_usd: null, notes: ["Cobertura compartida"],
            summary: [{label: "<Pendiente>", amount_usd: null}]}});
        await current;
        calls.at(-2).resolve({message: {modified: "V1", rows: [{document: "STALE"}], total_usd: 100}});
        await obsolete;
        assert.ok(!wrapper.value.includes("STALE"));
        assert.ok(wrapper.value.includes("—"));
        assert.ok(summary.value.includes("&lt;Pendiente>"));
        assert.ok(summary.value.includes("—"));
    }
    console.log("OK: linked-document tabs in both child tables, exact amounts, safe links, dirty forms and stale-response protection.");
})().catch(error => {console.error(error); process.exitCode = 1;});
