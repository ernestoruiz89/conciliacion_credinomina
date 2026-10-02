const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const directory = path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation");
const meta = JSON.parse(fs.readFileSync(path.join(directory, "cn_remittance_allocation.json"), "utf8"));
for (const name of ["deposit_date", "deposit_amount", "bank_account"]) {
    assert.equal(meta.fields.find(field => field.fieldname === name).in_list_view, 1);
}
assert.equal(meta.fields.find(field => field.fieldname === "deposit_amount").options, "deposit_currency");
for (const name of ["employer", "deposit_reference", "bank_account", "result"]) {
    assert.equal(meta.fields.find(field => field.fieldname === name).in_standard_filter, 1,
        `${name} must be available as a standard list filter`);
}
assert.equal(meta.fields.find(field => field.fieldname === "result").read_only, 1,
    "Filtering must not make the reconciliation result editable");
const context = vm.createContext({frappe: {listview_settings: {}}});
vm.runInContext(fs.readFileSync(path.join(directory, "cn_remittance_allocation_list.js"), "utf8"), context);
const settings = context.frappe.listview_settings[meta.name];
assert.ok(settings.add_fields.includes("deposit_currency"));
for (const custom of [false, true]) {
    const listview = {meta, list_view_settings: {total_fields: 4}, setup_columns() { this.rebuilt = true; },
        render_header(force) { this.header = force; }};
    if (custom) listview.list_view_settings.fields = JSON.stringify([{fieldname: "employer"}, {fieldname: "deposit_date"}]);
    settings.onload(listview);
    settings.onload(listview); // No duplicated columns after reinitialization.
    assert.ok(listview.list_view_settings.total_fields >= meta.fields.filter(field => field.in_list_view).length + 3);
    assert.ok(listview.rebuilt && listview.header);
    if (custom) {
        const names = JSON.parse(listview.list_view_settings.fields).map(field => field.fieldname);
        assert.deepEqual(names, ["employer", "deposit_date", "deposit_amount", "bank_account"]);
    } else assert.equal(listview.list_view_settings.fields, undefined);
}
console.log("OK: deposit date, original-currency amount and bank account columns; existing columns preserved.");
