const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
const context = vm.createContext({__: text => text, frappe: {pages: {"control-credinomina": {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.helpers = {stateLabel, applicationStatusLabel, renderPeriodCard, renderMonthSummary, styles}; })();"), context);
const {stateLabel, applicationStatusLabel, renderPeriodCard, renderMonthSummary, styles} = context.helpers;
for (const [operative, historical, label] of [
    ["conciliado", "historico_conciliado", "Conciliado"], ["parcial", "historico_parcial", "Parcial"],
    ["en_transito", "historico_pendiente", "Pendiente"], ["excedente", "historico_excedente", "Con excedente"],
    ["diferencia", "historico_excepcion", "Con diferencia"],
]) {
    assert.equal(stateLabel(operative), label);
    assert.equal(stateLabel(historical), label);
    assert.ok(styles().includes(`.cn-${operative}, .cn-${historical} {`));
    const card = renderPeriodCard({name: "P", reconciliation_mode: "Historica", control_state: historical});
    assert.ok(card.includes(`>${label}</span>`));
    assert.ok(!card.includes("Histórico conciliado"));
}
assert.equal(applicationStatusLabel("Aplicado y remitido"), "Aplicado y depositado");
assert.equal(applicationStatusLabel("Remitido, aplicacion parcial"), "Depositado, aplicación parcial");
assert.equal(applicationStatusLabel("Depósito parcial"), "Depósito parcial");
assert.ok(!source.includes('__("Remitido"'));
assert.ok(!source.includes('__("Remitido / deducido"'));
assert.match(renderMonthSummary([{reconciliation_mode: "Operativa", control_state: "conciliado"}], "E", "2026-09"), /Asignado \/ deducido/);
assert.match(renderMonthSummary([{reconciliation_mode: "Historica", control_state: "historico_conciliado"}], "E", "2025-04"), /Asignado \/ aplicado/);
console.log("OK: Depositado terminology and shared status labels/colors for both modalities without changing stored states.");
