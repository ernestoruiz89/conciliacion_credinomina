// Run with: node tests/test_remittance_picker.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const context = {
    __: value => value,
    frappe: { ui: { form: { on() {} } }, show_alert() {}, msgprint() {} },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);

async function run() {
    const Picker = vm.runInContext("RemittanceTargetPicker", context);
    const picker = Object.create(Picker.prototype);
    const rows = [
        { id: "a", historical_application: "APRIL", pending_cents: 4652, client_name: "Ana" },
        { id: "b", historical_application: "MAY", pending_cents: 7000, client_name: "Luis" },
    ];
    picker.data = { rows, available_cents: 10000, modified: "version1" };
    picker.selected = new Map();
    picker.select(rows[0]);
    picker.select(rows[1]);
    assert.equal(picker.total(), 10000);
    assert.equal(picker.selected.get("b"), 5348); // Last item gets a visible partial payment.
    assert.equal(picker.valid(), true);
    picker.select(rows[0]);
    assert.equal(picker.selected.size, 2); // Never duplicate when selecting another page.
    picker.selected.set("a", 4653);
    assert.equal(picker.valid(), false); // Exceeds pending and budget by a cent.
    picker.selected.set("a", NaN);
    assert.equal(picker.valid(), false);
    picker.selected.set("a", 4652);

    let additions = [];
    let hidden = false;
    let dirty = false;
    picker.frm = {
        doc: { name: "DEPOSIT", targets: [] },
        add_child(table, row) { assert.equal(table, "targets"); additions.push(row); },
        refresh_field() {}, dirty() { dirty = true; },
    };
    picker.dialog = { hide() { hidden = true; } };
    context.frappe.call = async () => ({ message: { ...picker.data, available_cents: 9999 } });
    await picker.apply();
    assert.equal(additions.length, 0); // A concurrent allocation cannot silently overbook cash.
    assert.equal(hidden, false);
    context.frappe.call = async () => ({ message: picker.data });
    await picker.apply();
    assert.equal(additions.length, 2);
    assert.equal(additions[0].amount_usd, 46.52);
    assert.equal(additions[1].amount_usd, 53.48);
    assert.equal(additions[0].historical_application, "APRIL");
    assert.equal(additions[0].period, "");
    assert.equal(dirty, true);
    assert.equal(hidden, true);
    // No save/submit/reconciliation method is provided; selection must only edit the form.

    const update = vm.runInContext("updateUsdEquivalent", context);
    let converted;
    update({ doc: { deposit_currency: "NIO", deposit_amount: 3653, fx_rate: 36.53 },
        set_value(field, value) { converted = value; } });
    assert.equal(converted, 100);
    update({ doc: { deposit_currency: "USD", deposit_amount: 46.52 },
        set_value(field, value) { converted = value; } });
    assert.equal(converted, 46.52);
    console.log("OK: partial selection, cents, duplicates, concurrency, form-only changes and FX.");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
