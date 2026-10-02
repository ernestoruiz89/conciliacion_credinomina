const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const handlers = {};
const context = { __: value => value, frappe: {ui: {form: {on(name, value) {handlers[name] = value;}}}} };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);

async function run() {
    const pending = [];
    context.frappe.call = args => new Promise(resolve => pending.push({args, resolve}));
    const load = vm.runInContext("loadPayingCompanies", context);
    const queries = {};
    const frm = {doc: {employer: "INDENICSA"}, fields_dict: {detail_rows: {grid: {df: {}}}},
        set_query(...args) {queries[args.slice(0, -1).join(".")] = args.at(-1);}};
    handlers["CN Remittance Allocation"].setup(frm);
    const first = load(frm);
    assert.equal(pending[0].args.args.employer, "INDENICSA");
    pending[0].resolve({message: ["INDENICSA", "CBC"]});
    await first;
    assert.deepEqual(Array.from(queries["period.detail_periods"]().filters.employer[1]), ["INDENICSA", "CBC"]);
    assert.deepEqual(Array.from(queries["employer.detail_rows"]().filters.name[1]), ["INDENICSA", "CBC"]);
    const old = load(frm);
    frm.doc.employer = "OTRA";
    const current = load(frm);
    pending[2].resolve({message: ["OTRA"]});
    await current;
    pending[1].resolve({message: ["INDENICSA", "CBC"]});
    await old;
    assert.deepEqual(Array.from(frm.paying_companies), ["OTRA"]);
    frm.doc.employer = "";
    await load(frm);
    assert.equal(frm.paying_companies.length, 0);
    console.log("OK: authorized company filters, safe defaults and stale lookup protection.");
}
run().catch(error => {console.error(error); process.exitCode = 1;});
