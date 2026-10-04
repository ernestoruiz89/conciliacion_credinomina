const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let html = "", options, requested;
const wrapper = {html(value) {html = value;}, append(value) {html += value;}, find() {return {remove() {}};}, on() {}};
const esc = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const context = vm.createContext({__: value => value, format_currency: value => `USD ${Number(value || 0).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}, Dialog: class {constructor(args) {options = args;} get_field() {return {$wrapper: wrapper};} show() {} hide() {}}},
        utils: {escape_html: esc}, datetime: {str_to_user: value => `DATE:${value}`},
        call: async args => {requested = args; return {message: {notice: "No retroactivo", next_start: 20, has_more: true, rows: [{
            user: "<operator>", date: "2026-10-03", action: "Conciliar depósito", reason: "<script>bad</script>",
            before: {allocated_usd: 50, allocation_detail: '[{"tipo":"Aplicacion historica","periodo":"P","importe_usd":50}]'},
            after: {allocated_usd: 80, allocation_detail: '[{"tipo":"Partida complementaria","partida":"<X>","importe_usd":80}]'},
        }]}};}}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js", "utf8"), context);
(async () => {
    await context.showReconciliationHistory({doc: {name: "DEP"}});
    assert.equal(requested.args.deposit_name, "DEP");
    assert.equal(options.size, "extra-large");
    assert.match(html, /USD 50\.00/);
    assert.match(html, /USD 80\.00/);
    assert.match(html, /DATE:2026-10-03/);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>|<operator>|<X>/);
    assert.match(html, /data-more-history/);
    console.log("OK: audit before/after, dates, pagination and escaped external text.");
})().catch(error => {console.error(error); process.exitCode = 1;});
