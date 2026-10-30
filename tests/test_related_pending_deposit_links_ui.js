const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
let html, dialog, navigated = false;
const context = vm.createContext({window: {}, __: text => text, format_currency: String, frappe: {
    utils: {escape_html: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;")},
    set_route() {navigated = true;},
    ui: {Dialog: function(options) {
        this.options = options;
        this.fields_dict = {related_html: {$wrapper: {html(value) {html = value;}}}};
        this.show = () => {};
        this.hide = () => {this.hidden = true;};
        dialog = this;
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/public/js/related_pending_modal.js", "utf8"), context);
context.window.credinominaShowRelatedPending({client_name: "Persona", applications: [
    {type: "Aplicación", document: "IMPORT", source_row: 10, amount_usd: 100, pending_usd: .01, status: "Depósito parcial",
        deposits: [{document: "DEP-1", date: "2026-02-01", reference: '"<REF>'},
                   {document: "DEP/2 <x>", via_collection: true}]},
    {type: "Aplicación", document: "UNASSIGNED", amount_usd: 50, pending_usd: 50},
], rows: []});
assert.ok(html.includes('/app/cn-remittance-allocation/DEP-1'));
assert.ok(html.includes('/app/cn-remittance-allocation/DEP%2F2%20%3Cx%3E'));
assert.ok(html.includes('DEP/2 &lt;x>'));
assert.ok(html.includes('title="2026-02-01 · &quot;&lt;REF>"'));
assert.equal((html.match(/Depósitos asignados/g) || []).length, 1, "Do not show empty link blocks for unassigned applications");
assert.ok(html.includes('vía cobranza'));
assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
assert.ok(html.includes('0.01'));
assert.ok(!dialog.hidden);
assert.equal(navigated, false, "Rendering links must not route away from the current modal or change its filters");
dialog.options.primary_action();
assert.equal(dialog.hidden, true);
console.log("OK: multiple deposit links, safe labels and routes, shared-collection hint, unchanged pending and modal navigation.");
