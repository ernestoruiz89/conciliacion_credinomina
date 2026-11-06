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
    picker.available_cents = picker.data.available_cents;
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

    const detailRow = {name: "DETAIL-1", source_row: 4, idx: 1, client_name: "Ana", pending_usd: 25, amount_usd: 25};
    const rowTargets = [];
    const rowPicker = Object.create(Picker.prototype);
    rowPicker.frm = {doc: {name: "DEPOSIT", detail_rows: [detailRow], targets: rowTargets},
        add_child(table, target) { assert.equal(table, "targets"); rowTargets.push(target); },
        refresh_field() {}, dirty() { dirty = true; }};
    rowPicker.detailRow = detailRow;
    rowPicker.detailRowName = detailRow.name;
    rowPicker.data = {rows: [{id: "app-ana", historical_application: "APP-ANA", pending_cents: 8000}],
        available_cents: 8000, modified: "version1", detail_row: detailRow.name};
    rowPicker.available_cents = rowPicker.availableFor(rowPicker.data);
    rowPicker.selected = new Map([["app-ana", 2500]]);
    rowPicker.dialog = {hide() { hidden = true; }};
    context.frappe.call = async request => {
        assert.equal(request.args.detail_row_name, "DETAIL-1");
        assert.deepEqual(JSON.parse(request.args.selected_ids), ["app-ana"]);
        return {message: rowPicker.data};
    };
    await rowPicker.apply();
    assert.equal(rowTargets[0].detail_row, "DETAIL-1");
    assert.equal(rowTargets[0].detail_row_label, "Fila 4 · Ana");
    assert.equal(rowTargets[0].amount_usd, 25);
    assert.equal(rowPicker.availableFor(rowPicker.data), 0); // Existing unprocessed linked targets reserve this row's remaining amount.

    // The same deposit already paid 0.01 of this application by FIFO on another
    // detail row. Add only its 18.66 remainder, linked to the selected 18.67 row.
    rowTargets.length = 0;
    detailRow.pending_usd = detailRow.amount_usd = 18.67;
    rowPicker.data = {rows: [{id: "app-ana", historical_application: "APP-ANA",
        applied_cents: 1867, assigned_cents: 1, pending_cents: 1866}],
        available_cents: 1867, modified: "version2", detail_row: detailRow.name};
    rowPicker.available_cents = rowPicker.availableFor(rowPicker.data);
    rowPicker.selected.clear();
    rowPicker.select(rowPicker.data.rows[0]);
    assert.equal(rowPicker.total(), 1866);
    assert.equal(rowPicker.valid(), true);
    await rowPicker.apply();
    assert.equal(rowTargets.length, 1);
    assert.equal(rowTargets[0].historical_application, "APP-ANA");
    assert.equal(rowTargets[0].detail_row, "DETAIL-1");
    assert.equal(rowTargets[0].amount_usd, 18.66);
    assert.equal(rowPicker.availableFor(rowPicker.data), 1);

    // A second destination supplements an existing manual target; it must
    // never edit or replace the 4.29 already linked to this application.
    rowTargets.length = 0;
    rowPicker.frm.doc.allocation_detail = "[]";
    detailRow.amount_usd = 40.05;
    detailRow.matched_targets = "[]";
    const originalTarget = {name:"CONFIRMED", historical_application:"APP-ANA", amount_usd:4.29, detail_row:detailRow.name};
    rowTargets.push(originalTarget);
    rowPicker.data = {rows:[{id:"app-ana", historical_application:"APP-ANA", applied_cents:4005,
        assigned_cents:429, pending_cents:3576, reserved_cents:0}], available_cents:3576,
        detail_available_cents:3576, modified:"manual-partial"};
    rowPicker.available_cents = rowPicker.availableFor(rowPicker.data);
    rowPicker.selected.clear();
    rowPicker.select(rowPicker.data.rows[0]);
    assert.equal(rowPicker.total(), 3576);
    await rowPicker.apply();
    assert.equal(rowTargets.length, 2);
    assert.equal(rowTargets[0], originalTarget);
    assert.equal(originalTarget.amount_usd, 4.29);
    assert.equal(rowTargets[1].historical_application, originalTarget.historical_application);
    assert.equal(rowTargets[1].amount_usd, 35.76);
    assert.equal(rowPicker.availableFor(rowPicker.data), 0);

    rowTargets.length = 0;
    detailRow.amount_usd = 71.51;
    detailRow.matched_targets = "[]";
    rowPicker.frm.doc.allocation_detail = JSON.stringify([{tipo: "Aplicacion historica", aplicacion_id: "OLD",
        fila_detalle: detailRow.name, importe_usd: 34.59, origen: "Automática FIFO"}]);
    rowPicker.data = {available_cents: 18094, detail_available_cents: 3692, rows: [
        {id: "NEW1", historical_application: "NEW1", pending_cents: 3459},
        {id: "NEW2", historical_application: "NEW2", pending_cents: 3459},
    ]};
    rowPicker.available_cents = rowPicker.availableFor(rowPicker.data);
    assert.equal(rowPicker.available_cents, 3692, "Reserve confirmed FIFO before proposing new destinations");
    rowPicker.selected.clear();
    rowPicker.select(rowPicker.data.rows[0]); rowPicker.select(rowPicker.data.rows[1]);
    assert.equal(rowPicker.selected.get("NEW1"), 3459);
    assert.equal(rowPicker.selected.get("NEW2"), 233);
    assert.equal(rowPicker.valid(), true);
    rowPicker.data.detail_available_cents = 100;
    assert.equal(rowPicker.availableFor(rowPicker.data), 100, "Use the server row cap during concurrent revalidation");

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
