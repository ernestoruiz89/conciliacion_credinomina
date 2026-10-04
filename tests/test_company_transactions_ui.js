const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = vm.createContext({__: value => value, frappe: {query_reports: {},
    datetime: {get_today: () => "2026-10-04"}, utils: {escape_html: value => String(value).replaceAll('"', "&quot;").replaceAll("<", "&lt;")}}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/report/transacciones_por_empresa/transacciones_por_empresa.js"), "utf8"), context);
const report = context.frappe.query_reports["Transacciones por Empresa"];
const filters = Object.fromEntries(report.filters.map(field => [field.fieldname, field]));
assert.equal(filters.year.default, 2026);
assert.equal(filters.transaction_type.default, "Aplicaciones");
assert.equal(filters.transaction_type.options, "Aplicaciones\nDepósitos");
assert.equal(filters.include_drafts.default, 0);
const formatter = (value, field, data) => report.formatter(value, null, {fieldname: field}, data, value => String(value));
for (const [state, color] of [["Conciliado", "#dcfce7"], ["Parcial", "#ffedd5"], ["Pendiente", "#fee2e2"]]) {
    const result = formatter(5, "m04", {m04_state: state, m04_conciliado: 3, m04_parcial: 1, m04_pendiente: 1});
    assert.ok(result.includes(color));
    assert.ok(result.includes("Conciliadas: 3"));
    assert.ok(result.includes("Parciales: 1"));
    assert.ok(result.includes("Pendientes: 1"));
    assert.ok(result.includes('aria-label="'));
    assert.ok(result.includes("width:calc(100% + 8px)"));
    assert.ok(result.includes("margin:-4px;padding:4px"));
    assert.ok(result.includes("box-sizing:border-box;text-align:right"));
    assert.ok(result.endsWith(">5</div>"));
    assert.ok(formatter(500, "m04", {m04_state: state}).includes(color));
}
assert.equal(formatter(8, "total", {}), '<div style="font-weight:bold;text-align:right">8</div>');
assert.equal(formatter("A", "employer", {}), "A");
assert.equal(formatter("", "employer", {}), "Sin empresa identificada");
assert.ok(formatter(0, "m01", {}).includes("Sin transacciones"));
assert.ok(!formatter(0, "m01", {}).includes("background"));
assert.equal(report.formatter("value", null, null, {}, value => value), "value");
assert.ok(!formatter(1, "m01", {m01_state: '<script>"'}).includes("<script>"));
for (const [percentage, halfCovered, color] of [[49.99, false, "#ffedd5"], [50, true, "#fef9c3"], [75, true, "#fef9c3"]]) {
    const result = formatter(5, "m04", {m04_state: "Parcial", m04_percentage: percentage,
        m04_half_covered: halfCovered, m04_total_usd: 100, m04_covered_usd: percentage});
    assert.ok(result.includes(color));
    assert.ok(result.includes("Importe conciliado"));
    assert.ok(result.includes(" / 100.00"));
}
assert.ok(formatter(5, "m04", {m04_state: "Parcial", m04_percentage: null, m04_half_covered: false}).includes("Porcentaje del importe no disponible"));
assert.ok(formatter(5, "m04", {m04_state: "Conciliado", m04_percentage: 100, m04_half_covered: true}).includes("#dcfce7"));
console.log("OK: monthly transaction filters, counts, traffic-light colors, tooltips, accessibility and empty cells.");
