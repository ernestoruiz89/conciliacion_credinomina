const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

let dialog;
const context = vm.createContext({
    __: (text, values = []) => text.replace(/\{(\d+)\}/g, (_, i) => values[i]),
    frappe: {
        ui: {form: {on() {}}, Dialog: function(options) {
            dialog = this;
            this.options = options;
            this.values = {};
            this.html = options.fields.find(field => field.fieldname === "exception_list").options;
            this.get_field = () => ({$wrapper: {
                html: value => {this.html = value;},
                on: (event, selector, handler) => {this.page = direction => handler({currentTarget: {getAttribute: () => direction}});},
            }});
            this.get_value = field => this.values[field] ?? "";
            this.set_values = async values => {Object.assign(this.values, values);};
            this.change = (field, value) => {
                this.values[field] = value;
                options.fields.find(entry => entry.fieldname === field).onchange();
            };
            this.show = () => {};
            this.hide = () => {};
        }},
        utils: {escape_html: value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")},
        call() {throw new Error("This modal must remain read-only");},
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_accounting_import/cn_accounting_import.js"), "utf8"), context);

(async () => {
    const row = {idx: 9, source_row: 98765, event_type: "Aplicacion", effective: 1,
        match_status: "Conciliado", deposit_match_status: "Depósito parcial", historical_period: "P1",
        amount_usd: 137.33, net_applied_usd: 137.33, historical_remitted_usd: 111.32, historical_balance_usd: 26.01,
        client_name: "Marión <script>", client_number: "123", loan_number: "108266-1", accounting_entry: "AS-42",
        receipt: "REC-66", deposit_match_reason: "Pendiente <b>26.01</b>"};
    const frm = {doc: {name: "CONTA-REPSA-4-2025-004", rows: [row]}};
    const before = JSON.stringify(frm);
    const amounts = context.importExceptionAmounts(row, frm.doc);
    assert.equal(amounts.amount, 13733);
    assert.equal(amounts.paid, 11132);
    assert.equal(amounts.pending, 2601);
    assert.equal(context.importExceptionAmounts({...row, application_adjustment_usd: 10, net_applied_usd: 127.33}, {}).net, 12733);
    const html = context.importExceptionsHtml(frm);
    for (const label of ["Monto US$", "Ajustes US$", "Aplicado neto US$", "Depositado US$", "Pendiente US$", "137.33", "111.32", "26.01", "<td>9</td>"]) {
        assert.ok(html.includes(label), label);
    }
    assert.ok(!html.includes("98765"));
    assert.ok(!html.includes("<script>"));
    assert.ok(html.includes("&lt;b&gt;26.01&lt;/b&gt;"));
    for (const search of ["MARION", "108266", "AS-42", "REC-66", "9 marion"]) {
        assert.equal(context.filterImportExceptions(frm, {search}).length, 1, search);
    }
    for (const filters of [{search: "No existe"}, {state: "Sin deposito"}, {min_amount: "138"}, {max_amount: "137.32"}, {search: "98765"}]) {
        assert.equal(context.filterImportExceptions(frm, filters).length, 0);
    }
    assert.equal(context.filterImportExceptions(frm, {state: "Depósito parcial", stage: "Depósito de la aplicación", min_amount: "137.33", max_amount: "137.33"}).length, 1);
    for (const filters of [{min_amount: "abc"}, {min_amount: "1,000"}, {min_amount: "10", max_amount: "5"}]) {
        assert.ok(context.importExceptionsHtml(frm, filters).includes('role="alert"'));
    }
    assert.equal(context.filterImportExceptions(frm, {min_amount: "0"}).length, 1);
    assert.equal(context.filterImportExceptions(frm, {max_amount: "0"}).length, 0);
    const operational = context.importExceptionAmounts({...row, historical_period: ""}, {});
    assert.equal(operational.paid, null);
    assert.equal(operational.pending, null); // Shared operational coverage must not be guessed.
    const deposit = context.importExceptionAmounts({event_type: "Deposito", amount_usd: 100, unallocated_usd: 10}, {});
    assert.equal(deposit.paid, 10000);
    assert.equal(deposit.pending, 1000);
    assert.equal(deposit.net, null);
    assert.equal(context.importExceptionRows([{...row, deposit_match_status: "Conciliada: depósito + ajuste"}]).length, 0);
    assert.equal(JSON.stringify(frm), before);

    frm.doc.rows = Array.from({length: 120}, (_, index) => ({...row, idx: index + 1, client_name: `Persona ${index + 1}`}));
    context.showImportExceptions(frm);
    assert.ok(dialog.html.includes("Mostrando 1–50 de 120"));
    assert.equal((dialog.html.match(/<td>\d+<\/td>/g) || []).length, 50);
    dialog.page("next");
    assert.ok(dialog.html.includes("Mostrando 51–100 de 120"));
    dialog.page("next");
    assert.ok(dialog.html.includes("Mostrando 101–120 de 120"));
    assert.equal((dialog.html.match(/<td>\d+<\/td>/g) || []).length, 20);
    dialog.page("next");
    assert.ok(dialog.html.includes("Mostrando 101–120 de 120"));
    dialog.page("previous");
    assert.ok(dialog.html.includes("Mostrando 51–100 de 120"));
    dialog.change("search", "Persona 120");
    assert.ok(dialog.html.includes("Mostrando 1–1 de 1"));
    dialog.change("state", "Sin deposito");
    assert.ok(dialog.html.includes("No hay filas que coincidan"));
    await dialog.options.secondary_action();
    assert.ok(dialog.html.includes("Mostrando 1–50 de 120"));
    console.log("Import exception columns, filters and pagination checks passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
