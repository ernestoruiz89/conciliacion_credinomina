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
    {type: "Aplicación", document: "IMPORT", date: "2026-01-31 23:59:59", source_row: 10, amount_usd: 100, pending_usd: .01, status: "Depósito parcial",
        core_entry: {voucher: "00041", reference: "0010", description: 'Pago "<cliente>" & ajuste'},
        deposits: [{document: "DEP-1", date: "2026-02-01", reference: '"<REF>'},
                   {document: "DEP/2 <x>", via_collection: true}],
        complementary_items: [{document: "CN-COMP-1", date: "2026-02-15", reference: '"<CORE>'},
                              {document: "CN/COMP <2>"}]},
    {type: "Aplicación", document: "IMPORT", source_row: 11, amount_usd: 50, pending_usd: 50,
        core_entry: {voucher: "00042", description: "Pago de otra fila"},
        observations: ['Distribución <compartida> & pendiente']},
], rows: [
    {type: "Partida complementaria", document: "CORE-DEBIT", direction: "Débito", date: "2026-02-15", amount_usd: 13.08, pending_usd: 13.08},
    {type: "Partida complementaria", document: "CORE-CREDIT", direction: "Crédito", date: "2026-02-15", amount_usd: 20, pending_usd: 20},
    {type: "Depósito", document: "DEP-1", amount_usd: 1, pending_usd: 1},
], core_entries: {
    deposits: {"DEP-1": [{voucher: "00051", reference: "CORE-A", description: "Primer origen"},
                         {voucher: "00052", reference: "CORE-B", description: "Segundo origen"}]},
    complementary_items: {"CN-COMP-1": [{voucher: "00061", reference: "NC-01", description: "Ajuste vinculado"}],
                          "CORE-DEBIT": [{voucher: "00062", reference: "ND-02", description: "Nota de débito"}]},
}});
function titles(path) {
    return [...html.matchAll(new RegExp('href="/app/' + path + '"[^>]*?title="([^"]*)"', "g"))].map(match => match[1]);
}
assert.deepEqual(titles("cn-accounting-import/IMPORT"), [
    "Número de asiento: 00041\nNúmero de referencia: 0010\nDescripción: Pago &quot;&lt;cliente>&quot; &amp; ajuste",
    "Número de asiento: 00042\nNúmero de referencia: —\nDescripción: Pago de otra fila",
]);
const depositCore = "Número de asiento: 00051\nNúmero de referencia: CORE-A\nDescripción: Primer origen\n\n" +
    "Número de asiento: 00052\nNúmero de referencia: CORE-B\nDescripción: Segundo origen";
assert.deepEqual(titles("cn-remittance-allocation/DEP-1"), [depositCore + '\n\n01/02/2026 · &quot;&lt;REF>', depositCore]);
assert.deepEqual(titles("cn-complementary-item/CN-COMP-1"), [
    "Número de asiento: 00061\nNúmero de referencia: NC-01\nDescripción: Ajuste vinculado\n\n15/02/2026 · &quot;&lt;CORE>",
]);
assert.deepEqual(titles("cn-complementary-item/CORE-DEBIT"), ["Número de asiento: 00062\nNúmero de referencia: ND-02\nDescripción: Nota de débito"]);
assert.deepEqual(titles("cn-complementary-item/CORE-CREDIT"), ["Sin datos del asiento del core."]);
assert.ok(html.includes('Partida complementaria · <strong>Débito</strong>'));
assert.ok(html.includes('Partida complementaria · <strong>Crédito</strong>'));
assert.ok(html.includes('/app/cn-remittance-allocation/DEP-1'));
assert.ok(html.includes('/app/cn-remittance-allocation/DEP%2F2%20%3Cx%3E'));
assert.ok(html.includes('DEP/2 &lt;x>'));
assert.ok(html.includes('31/01/2026'));
assert.equal((html.match(/Depósitos asignados/g) || []).length, 1, "Do not show empty link blocks for unassigned applications");
assert.equal((html.match(/Partidas complementarias vinculadas/g) || []).length, 1);
assert.ok(html.includes('/app/cn-complementary-item/CN-COMP-1'));
assert.ok(html.includes('/app/cn-complementary-item/CN%2FCOMP%20%3C2%3E'));
assert.ok(html.includes('CN/COMP &lt;2>'));
assert.ok(html.includes('vía cobranza'));
assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
assert.ok(html.includes('0.01'));
assert.ok(html.includes('Distribución &lt;compartida> &amp; pendiente'));
assert.ok(!dialog.hidden);
assert.equal(navigated, false, "Rendering links must not route away from the current modal or change its filters");
dialog.options.primary_action();
assert.equal(dialog.hidden, true);
console.log("OK: per-row core tooltips, multiple deposit origins, linked items, safe text and modal navigation.");
