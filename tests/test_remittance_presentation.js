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
for (const result of ["Saldo a favor documentado", "Parcial con saldo a favor", "Conciliado con saldo a favor del cliente"]) {
    const state = present({docstatus: 1, result, amount_usd: 100, allocated_usd: 80, justified_surplus_usd: 20,
        unallocated_usd: 20, detail_count: 0, targets: []});
    assert.equal(state.result, "Conciliado con saldo a favor");
    assert.equal(state.color, "green");
    assert.match(state.hint, /no significa haberlo devuelto/);
    assert.doesNotMatch(state.hint, /pendiente de detalle por cliente/);
}
for (const amount of [99.99, 100.01]) {
    const state = present({docstatus: 1, result: "Conciliado", amount_usd: amount, allocated_usd: 100});
    assert.equal(state.result, "Por revisar");
    assert.equal(state.color, "orange", "Stale stored status cannot hide a cash difference");
}
assert.equal(present({docstatus: 1, result: "Conciliado", amount_usd: 100, allocated_usd: 100,
    detail_status: "Revisar filas"}).color, "orange");
const amounts = vm.runInContext("remittanceCashAmounts", context);
assert.equal(amounts({amount_usd: 100, allocated_usd: 80, justified_surplus_usd: 20}).pending, 0);
assert.equal(amounts({amount_usd: 100, allocated_usd: 70, justified_surplus_usd: 20}).pending, 10);
assert.equal(amounts({amount_usd: 46.53, allocated_usd: 46.52, justified_surplus_usd: .01}).pending, 0);
context.frappe.listview_settings = {};
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation_list.js"), "utf8"), context);
const indicator = context.frappe.listview_settings["CN Remittance Allocation"].get_indicator;
for (const docstatus of [0, 1, 2]) {
    for (const result of ["Pendiente", "Conciliado", "Conciliado con saldo a favor del cliente", "Saldo a favor documentado", "Parcial con saldo a favor", "Revisar detalle"]) {
        for (const credit of [0, 20]) {
            for (const amount of [80, 100, 101]) {
                const doc = {docstatus, result, amount_usd: amount, allocated_usd: 80, justified_surplus_usd: credit};
                const [label, color] = indicator(doc), state = present(doc);
                assert.equal(label, state.result, JSON.stringify(doc));
                assert.equal(color, state.color, JSON.stringify(doc));
            }
        }
    }
}
assert.match(table("broken JSON"), /datos originales se conservan/);
assert.match(table("[]"), /Todavía no hay/);
const html = table(JSON.stringify([{ tipo: "Aplicacion historica", periodo: "PER-1", credito: '<img src=x onerror="bad()">', importe_usd: 22.52, origen: "Manual" }]));
assert.match(html, /22\.52/);
assert.match(html, /Manual/);
assert.match(html, /&lt;img/);
assert.ok(!html.includes("<img"));
console.log("OK: status guidance, draft/cancelled/dirty states, readable allocations and escaped content.");
