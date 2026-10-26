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
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    for (const asynchronous of [false, true]) for (const personalized of [false, true]) {
        const meta = {fields: [{fieldname: "amount_usd", label: "Equivalente US$", in_list_view: 1},
            {fieldname: "employer", label: "Empresa", in_list_view: 1}]};
        const list = {meta, data: [{name: "A", docstatus: 1}], page: {add_inner_button() {}},
            list_view_settings: {total_fields: 2}, headers: 0, rendered: 0,
            render_header() {this.headers++;}, render() {this.rendered++;},
            get_header_html() {return this.columns.map(col => `<span data-sort-by="${col.df.fieldname}">${col.df.label}</span>`).join("");},
            setup_columns() {
                const build = () => {this.columns = meta.fields.filter(field => !personalized || field.fieldname === "employer")
                    .map(df => ({type: "Field", df}));};
                if (asynchronous) return Promise.resolve().then(build);
                build();
            }};
        if (personalized) list.list_view_settings.fields = '[{"fieldname":"employer"}]';
        settings.onload(list);
        await tick();
        const names = () => Array.from(list.columns, column => column.df.fieldname);
        for (const name of ["amount_usd", "cn_list_applied_usd", "cn_list_pending_usd"]) assert.equal(names().filter(x => x === name).length, 1);
        assert.ok(names().includes("employer"));
        const amountIndex = names().indexOf("amount_usd");
        assert.deepEqual(names().slice(amountIndex, amountIndex + 3), ["amount_usd", "cn_list_applied_usd", "cn_list_pending_usd"]);
        assert.deepEqual(Array.from(list.columns.slice(amountIndex, amountIndex + 3), col => col.df.label), ["Importe USD", "Aplicado USD", "Pendiente USD"]);
        assert.equal(meta.fields[0].label, "Equivalente US$", "the form metadata remains unchanged");
        assert.ok(!list.get_header_html().includes('data-sort-by="cn_list_'), "computed fields must not trigger SQL sorting");
        assert.ok(list.get_header_html().includes('data-sort-by="amount_usd"'));
        await list.setup_columns();
        settings.onload(list);
        assert.equal(names().filter(name => name === "cn_list_pending_usd").length, 1);
        list.settings.before_render();
        assert.ok(settings.formatters.cn_list_applied_usd(null, {}, list.data[0]).includes("—"));
        calls.shift().resolve({message: {A: {original_usd: -100, used_usd: 40, pending_usd: 60, state: "Parcial"}}});
        await tick();
        assert.equal(list.rendered, 1);
        assert.equal(settings.formatters.amount_usd(0, {}, list.data[0]), "USD -100.00");
        assert.equal(settings.formatters.cn_list_applied_usd(null, {}, list.data[0]), "USD 40.00");
        assert.equal(settings.formatters.cn_list_pending_usd(null, {}, list.data[0]), "USD 60.00");
        assert.equal(list.data[0].cn_list_applied_usd, 40);
        list.data[0]._cn_balance.pending_usd = 0;
        assert.equal(settings.formatters.cn_list_pending_usd(null, {}, list.data[0]), "USD 0.00");
        list.settings.before_render();
        calls.shift().reject(Error("no evidence access"));
        await tick();
        assert.ok(settings.formatters.cn_list_pending_usd(null, {}, list.data[0]).includes("Saldo no disponible"));
        assert.equal(list.data[0].cn_list_pending_usd, null);
    }
    assert.ok(!settings.add_fields.some(field => field.startsWith("cn_list_")), "calculated columns must not be requested from SQL");
    console.log("OK: three USD columns, synchronous/asynchronous and personalized lists, live balances and unknown values.");
})().catch(error => {console.error(error); process.exitCode = 1;});
