// Run with: node tests/test_deposit_distribution_ui.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const calls = [], events = {}, states = {}, html = {};
const controls = {
    "[data-distribution-search]": {value: ""},
    "[data-distribution-kind]": {value: ""},
};
const wrapper = {
    content: "",
    html(value) { this.content = value; return this; },
    off() { Object.keys(events).forEach(key => delete events[key]); return this; },
    on(event, selector, callback) { events[selector] = callback; return this; },
    find(selector) { return {
        val: () => controls[selector]?.value || "",
        html: value => { html[selector] = value; },
        prop: (name, value) => { states[`${selector}.${name}`] = value; },
    }; },
};
let responder;
const context = {
    __: (value, args = []) => value.replace(/\{(\d+)\}/g, (_match, index) => args[index]),
    frappe: {
        ui: {form: {on() {}}},
        utils: {escape_html: value => String(value).replace(/[&<>"']/g, char =>
            ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char])},
        call: args => { calls.push(args); return responder(args); },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const table = vm.runInContext("remittanceDistributionHtml", context);
const render = vm.runInContext("renderRemittanceDistribution", context);
const data = {
    docstatus: 1, total_usd: 120, distributed_usd: 120, detailed_usd: 120, pending_usd: 0, consistent: true,
    rows: [
        {category: "Pago a crédito", client_name: "Ána Pérez", client_number: "7", loan_number: "123-1", employer: "E", amount_usd: 90, state: "Distribuido", record_doctype: "CN Reconciliation Period", record_name: "P1"},
        {category: "Cobranza administrativa", amount_usd: 10, state: "Distribuido", record_doctype: "CN Complementary Item", record_name: "X1"},
        {category: "Saldo a favor del cliente", client_name: "Ána Pérez", amount_usd: 10, state: "Documentado", management_status: "Parcialmente resuelto", management_pending_usd: 6, record_doctype: "CN Complementary Item", record_name: "CLIENT1"},
        {category: "Saldo a favor de la empresa", employer: "E", amount_usd: 10, state: "Documentado", record_doctype: "CN Complementary Item", record_name: "COMPANY1"},
    ],
};
function form() {
    return {doc: {name: "DEP1", docstatus: 1}, fields_dict: {complete_distribution: {$wrapper: wrapper}},
        dirty: false, is_new: () => false, is_dirty() { return this.dirty; },
        save() { throw new Error("Read-only view must not save"); },
        set_value() { throw new Error("Read-only view must not alter fields"); }};
}

(async () => {
    const all = table(data);
    assert.match(all, /Saldo a favor del cliente/);
    assert.match(all, /Saldo a favor de la empresa/);
    assert.match(all, /Pendiente de gestión/);
    assert.match(all, /Gestión: Parcialmente resuelto/);
    assert.match(all, /\/app\/cn-complementary-item\/CLIENT1/);
    assert.match(all, /\/app\/cn-reconciliation-period\/P1/);
    const filtered = table(data, [data.rows[2]]);
    assert.match(filtered, /Total detallado del depósito \(todos los registros\)/);
    assert.match(filtered, /120\.00/);
    assert.match(filtered, /1 de 4 registros/);
    assert.match(table({...data, consistent: false}), /detalle no coincide/);
    const signed = table({...data, rows: [{category: "Ajuste", amount_usd: -10, difference_usd: -0.01}]});
    assert.match(signed, /-10\.00/);
    assert.match(signed, /-0\.01/);
    const malicious = table({...data, rows: [{category: '<script>bad()</script>', description: '" onmouseover="bad', client_name: "<img src=x>", record_doctype: "javascript:bad()", record_name: "bad", amount_usd: 1}]});
    assert.ok(!malicious.includes("<script>"));
    assert.ok(!malicious.includes("<img"));
    assert.ok(!malicious.includes("href=\"javascript:"));
    assert.match(malicious, /&quot; onmouseover=&quot;/);
    const many = {...data, rows: Array.from({length: 201}, (_, n) => ({category: `row${n}`, amount_usd: 1}))};
    assert.ok(!table(many).includes("row100</td>"));
    assert.match(table(many, many.rows, 1), /row100/);
    assert.match(table(many, many.rows, 2), /Página 3 de 3/);

    responder = async () => ({message: data});
    const frm = form();
    await render(frm);
    assert.equal(calls[0].method, "credinomina_reconciliation.deposit_distribution.get_distribution");
    assert.equal(calls[0].args.remittance_name, "DEP1");
    assert.match(html["[data-distribution-table]"], /Pago a crédito/);
    controls["[data-distribution-search]"].value = "ana perez";
    events["[data-distribution-search], [data-distribution-kind]"]();
    assert.match(html["[data-distribution-table]"], /2 de 4 registros/);
    controls["[data-distribution-kind]"].value = "Saldo a favor del cliente";
    frm.dirty = true;
    events["[data-distribution-search], [data-distribution-kind]"]();
    assert.match(html["[data-distribution-table]"], /1 de 4 registros/);
    assert.equal(states["[data-distribution-dirty].hidden"], false);

    for (const docstatus of [0, 2]) {
        const before = calls.length, other = form(); other.doc.docstatus = docstatus;
        await render(other);
        assert.equal(calls.length, before);
        assert.match(wrapper.content, /no tiene distribución activa|todavía no son pagos/);
    }
    responder = async () => { throw new Error("Server error"); };
    await render(frm);
    assert.match(wrapper.content, /Actualizar distribución para reintentar/);
    responder = async () => ({message: data});
    await render(frm);
    assert.match(wrapper.content, /Buscar cliente/);
    responder = async () => ({message: {...data, docstatus: 2}});
    await render(frm);
    assert.match(wrapper.content, /fue cancelado/);

    let complete;
    responder = () => new Promise(resolve => { complete = resolve; });
    const pending = render(frm);
    const replacement = form(); replacement.doc.docstatus = 0;
    frm.doc = replacement.doc;
    await render(frm);
    const currentContent = wrapper.content;
    complete({message: data}); await pending;
    assert.equal(wrapper.content, currentContent, "stale response cannot overwrite another deposit");
    console.log("OK: complete read-only distribution, signed items, customer/company credits, links, totals, filtering, pagination, permissions feedback, retry and stale responses.");
})().catch(error => { console.error(error); process.exitCode = 1; });
