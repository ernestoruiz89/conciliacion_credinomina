const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const calls = [];
const context = vm.createContext({__: text => text, frappe: {
    listview_settings: {}, utils: {escape_html: value => String(value)},
    format: (value, df, options, doc) => `${doc.usd_currency} ${Number(value).toFixed(df.precision)}`,
    call: args => new Promise((resolve, reject) => calls.push({args, resolve, reject})),
}});
const directory = path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item");
vm.runInContext(fs.readFileSync(path.join(directory, "cn_complementary_item_list.js"), "utf8"), context);
const settings = context.frappe.listview_settings["CN Complementary Item"];
const meta = JSON.parse(fs.readFileSync(path.join(directory, "cn_complementary_item.json"), "utf8"));
const stateField = meta.fields.find(field => field.fieldname === "reconciliation_status");
assert.equal(stateField.in_standard_filter, 1);
assert.equal(stateField.read_only, 1);
assert.equal(stateField.search_index, 1);
assert.ok(settings.add_fields.includes("reconciliation_status"));
for (const name of ["applied_usd", "pending_usd"]) {
    const field = meta.fields.find(field => field.fieldname === name);
    for (const flag of ["read_only", "in_standard_filter", "search_index", "in_list_view", "allow_on_submit"])
        assert.equal(field[flag], 1, `${name}.${flag}`);
    assert.equal(field.fieldtype, "Currency");
    assert.ok(settings.add_fields.includes(name));
}
for (const state of stateField.options.split("\n")) {
    const indicator = settings.get_indicator({reconciliation_status: state, _cn_balance: {state: "outdated"}});
    assert.equal(indicator[0], state);
    assert.equal(indicator[2], `reconciliation_status,=,${state}`);
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    for (const asynchronous of [false, true]) for (const personalized of [false, true]) {
        const meta = {fields: [{fieldname: "amount_usd", label: "Equivalente US$", in_list_view: 1},
            {fieldname: "employer", label: "Empresa", in_list_view: 1},
            ...["applied_usd", "pending_usd"].map(fieldname => ({fieldname, in_list_view: 1}))]};
        const list = {meta, data: [{name: "A", docstatus: 1, reconciliation_status: "Parcial", applied_usd: 40, pending_usd: 60}], page: {add_inner_button() {}},
            list_view_settings: {total_fields: 2}, headers: 0, rendered: 0,
            render_header() {this.headers++;}, render() {this.rendered++;},
            get_header_html() {return this.columns.map(col => `<span data-sort-by="${col.df.fieldname}">${col.df.label}</span>`).join("");},
            setup_columns() {
                const build = () => {this.columns = meta.fields.filter(field => !personalized || field.fieldname === "employer")
                    .map(df => ({type: "Field", df}));};
                if (asynchronous) return Promise.resolve().then(build);
                build();
            }};
        if (personalized) list.list_view_settings.fields = '[{"fieldname":"employer"},{"fieldname":"cn_list_applied_usd"},{"fieldname":"applied_usd"},{"fieldname":"cn_list_pending_usd"}]';
        settings.onload(list);
        await tick();
        const names = () => Array.from(list.columns, column => column.df.fieldname);
        for (const name of ["amount_usd", "applied_usd", "pending_usd"]) assert.equal(names().filter(x => x === name).length, 1);
        assert.ok(names().includes("employer"));
        const amountIndex = names().indexOf("amount_usd");
        assert.deepEqual(names().slice(amountIndex, amountIndex + 3), ["amount_usd", "applied_usd", "pending_usd"]);
        assert.deepEqual(Array.from(list.columns.slice(amountIndex, amountIndex + 3), col => col.df.label), ["Importe USD", "Aplicado USD", "Pendiente USD"]);
        assert.equal(meta.fields[0].label, "Equivalente US$", "the form metadata remains unchanged");
        for (const name of ["applied_usd", "pending_usd"])
            assert.ok(list.get_header_html().includes(`data-sort-by="${name}"`), "stored amounts support SQL sorting");
        if (personalized) {
            const saved = JSON.parse(list.list_view_settings.fields).map(field => field.fieldname);
            assert.equal(saved.filter(name => name === "applied_usd").length, 1);
            assert.ok(!saved.some(name => name.startsWith("cn_list_")));
        }
        assert.ok(list.get_header_html().includes('data-sort-by="amount_usd"'));
        await list.setup_columns();
        settings.onload(list);
        assert.equal(names().filter(name => name === "pending_usd").length, 1);
        list.settings.before_render();
        assert.equal(settings.formatters.applied_usd(list.data[0].applied_usd, {}, list.data[0]), "USD 40.00");
        calls.shift().resolve({message: {A: {original_usd: -100, used_usd: 99, pending_usd: 1, state: "Parcial"}}});
        await tick();
        assert.equal(list.rendered, 1);
        assert.equal(settings.formatters.amount_usd(0, {}, list.data[0]), "USD -100.00");
        assert.equal(settings.formatters.applied_usd(list.data[0].applied_usd, {}, list.data[0]), "USD 40.00");
        assert.equal(settings.formatters.pending_usd(list.data[0].pending_usd, {}, list.data[0]), "USD 60.00");
        assert.equal(list.data[0].applied_usd, 40, "async evidence cannot overwrite the values used by SQL filters");
        list.settings.before_render();
        calls.shift().reject(Error("no evidence access"));
        await tick();
        assert.equal(settings.formatters.pending_usd(list.data[0].pending_usd, {}, list.data[0]), "USD 60.00");
        assert.equal(list.data[0].pending_usd, 60);
    }
    for (const field of ["applied_usd", "pending_usd"]) {
        assert.equal(settings.formatters[field](0, {}, {reconciliation_status: "Conciliado"}), "USD 0.00");
        assert.ok(settings.formatters[field](null, {}, {}).includes("—"));
        assert.ok(settings.formatters[field](0, {}, {reconciliation_status: "Saldo no disponible"}).includes("Saldo no disponible"));
    }
    assert.ok(!settings.add_fields.some(field => field.startsWith("cn_list_")));
    console.log("OK: persisted filterable/sortable USD fields, saved layouts, stable values and unknown balances.");
})().catch(error => {console.error(error); process.exitCode = 1;});
