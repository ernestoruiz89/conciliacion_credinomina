const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const handlers = {};
const pending = [];
let templateUrl;
const context = vm.createContext({URLSearchParams, window: {open: url => {templateUrl = url;}}, frappe: {
    ui: {form: {on: (doctype, events) => {handlers[doctype] = events;}}},
    db: {get_value: (doctype, name, field) => {
        assert.equal(doctype, "CN Bank Account");
        assert.equal(field, "currency");
        return new Promise(resolve => pending.push({name, resolve}));
    }},
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const events = handlers["CN Remittance Allocation"];
const frm = {
    doc: {docstatus: 0, bank_account: "NIO account", deposit_currency: "USD", deposit_amount: 824.78, fx_rate: 36.6243},
    async set_value(field, value) {
        this.doc[field] = value;
        if (field === "deposit_currency") events.deposit_currency(this);
    },
};

(async () => {
    context.downloadRemittanceTemplate({doc: {name: "CN-ALLOC-2026-00001", detail_periods: [{period: "P-1"}]}, is_new: () => false});
    const params = new URL(templateUrl, "https://example.test").searchParams;
    assert.equal(params.get("remittance_name"), "CN-ALLOC-2026-00001");
    assert.deepEqual(JSON.parse(params.get("period_names")), ["P-1"]);
    context.downloadRemittanceTemplate({doc: {name: "new-document"}, is_new: () => true});
    assert.equal(new URL(templateUrl, "https://example.test").searchParams.has("remittance_name"), false);

    let task = events.bank_account(frm);
    pending.shift().resolve({message: {currency: "NIO"}});
    await task;
    assert.equal(frm.doc.deposit_currency, "NIO");
    assert.equal(frm.doc.amount_usd, 22.52);

    const stale = events.bank_account(frm);
    frm.doc.bank_account = "USD account";
    task = events.bank_account(frm);
    const oldRequest = pending.shift();
    pending.shift().resolve({message: {currency: "USD"}});
    await task;
    oldRequest.resolve({message: {currency: "NIO"}});
    await stale;
    assert.equal(frm.doc.deposit_currency, "USD");
    assert.equal(frm.doc.amount_usd, 824.78);
    assert.equal(frm.doc.fx_rate, 36.6243); // May still be needed for NIO detail rows.

    task = events.bank_account(frm);
    frm.doc.bank_account = "";
    pending.shift().resolve({message: {currency: "NIO"}});
    await task;
    assert.equal(frm.doc.deposit_currency, "USD");
    await events.bank_account(frm);
    assert.equal(pending.length, 0);
    frm.doc.bank_account = "NIO account";
    for (const docstatus of [1, 2]) {
        frm.doc.docstatus = docstatus;
        await events.bank_account(frm);
        assert.equal(pending.length, 0);
    }
    frm.doc.docstatus = 0;
    task = events.bank_account(frm);
    frm.doc = {...frm.doc, name: "Another document"};
    pending.shift().resolve({message: {currency: "NIO"}});
    await task;
    assert.equal(frm.doc.deposit_currency, "USD");
    console.log("OK: account currency, USD conversion, stale lookups and confirmed/cancelled documents.");
})().catch(error => {console.error(error); process.exitCode = 1;});
