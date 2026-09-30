// Run with: node tests/test_remittance_presentation.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = {
    __: value => value,
    frappe: {
        ui: { form: { on() {} } },
        utils: { escape_html: value => String(value).replace(/[&<>"']/g, char =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]) },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const present = vm.runInContext("remittancePresentation", context);
const table = vm.runInContext("remittanceAllocationHtml", context);
const review = present({ docstatus: 1, result: "Revisar detalle", allocated_usd: 22.52, unallocated_usd: 0 });
assert.equal(review.result, "Revisar detalle");
assert.notEqual(review.color, "green");
assert.match(review.hint, /dinero está distribuido.*revisión no ha terminado/);
assert.equal(present({ docstatus: 0, result: "Conciliado" }).result, "Sin conciliar");
assert.equal(present({ docstatus: 2, result: "Conciliado" }).result, "No participa");
assert.equal(present({ docstatus: 1, result: "Conciliado" }).color, "green");
assert.match(present({ docstatus: 1, result: "Conciliado" }, true).hint, /sin guardar/);
assert.notEqual(present({ docstatus: 1, result: "Conciliado" }, true).color, "green");
assert.match(table("broken JSON"), /datos originales se conservan/);
assert.match(table("[]"), /Todavía no hay/);
const html = table(JSON.stringify([{ tipo: "Aplicacion historica", periodo: "PER-1", credito: '<img src=x onerror="bad()">', importe_usd: 22.52, origen: "Manual" }]));
assert.match(html, /22\.52/);
assert.match(html, /Manual/);
assert.match(html, /&lt;img/);
assert.ok(!html.includes("<img"));
console.log("OK: status guidance, draft/cancelled/dirty states, readable allocations and escaped content.");
