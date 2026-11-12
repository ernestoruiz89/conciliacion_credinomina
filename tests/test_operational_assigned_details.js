const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
let dialog, html, listener;
const requests = [];
const context = vm.createContext({__: text => text, format_currency: value => String(value), frappe: {
    query_reports: {}, utils: {escape_html: esc}, datetime: {str_to_user: value => value},
    call(options) { return new Promise((resolve, reject) => requests.push({options, resolve, reject})); },
    ui: {Dialog: class {
        constructor(options) { this.options = options; this.fields_dict = {details: {$wrapper: {html(value) { html = value; }}}}; dialog = this; }
        show() {} hide() {this.options.onhide();}
    }},
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_operativo/estado_de_cuenta_operativo.js', 'utf8'), context);
const settings = context.frappe.query_reports['Estado de Cuenta Operativo'];
const row = {position_type: 'Aplicación', source_rows: 'A, B', period: 'P', collection_row_id: 'COL', assigned_usd: 100};
const formatter = data => settings.formatter(data.assigned_usd, 0, {fieldname: 'assigned_usd', fieldtype: 'Currency'}, data, () => '100.00');
assert.match(formatter(row), /data-cn-assigned/);
assert.match(formatter({...row, assigned_usd: 0}), /data-cn-assigned/);
for (const data of [{...row, is_total_row: 1}, {...row, assigned_usd: null}, {...row, position_type: 'Cobranza'}]) {
    assert.doesNotMatch(formatter(data), /data-cn-assigned/);
}
let filters = {cutoff_date: '2026-08-31', client_number: '1'};
const report = {get_filter_values: () => filters, get_filter: () => ({get_value: () => '', $wrapper: {toggle() {}}}),
    page: {wrapper: {off() {return this;}, on(event, selector, callback) {listener = callback;}},
        add_inner_button() {return {text() {}};}}};
settings.onload(report);
(async () => {
    let prevented = false;
    listener({preventDefault() {prevented = true;}, currentTarget: {getAttribute: () => encodeURIComponent(JSON.stringify(row))}});
    assert.equal(prevented, true);
    assert.equal(requests.length, 1);
    assert.match(requests[0].options.method, /operational_assigned_details.get_details$/);
    assert.equal(JSON.parse(requests[0].options.args.filters).cutoff_date, '2026-08-31');
    requests[0].resolve({message: {assigned_usd: 100, total_usd: 100, cutoff_date: '2026-08-31', client_name: '<img onerror=x>',
        rows: [{doctype: 'CN Remittance Allocation', document: 'DEP <1>', kind: 'Depósito', state: 'Conciliado', amount_usd: 90},
            {doctype: 'CN Complementary Item', document: 'COMP', kind: 'Complementaria', state: 'Confirmado', amount_usd: 10}],
        adjustments: [{doctype: 'CN Complementary Item', document: 'ADJUST', amount_usd: 5}], notes: ['<script>bad</script>']}});
    await new Promise(resolve => setImmediate(resolve));
    assert.match(html, /cn-remittance-allocation\/DEP%20%3C1%3E/);
    assert.match(html, /cn-complementary-item\/COMP/);
    assert.match(html, /2026-08-31/);
    assert.match(html, /Total del desglose/);
    assert.match(html, /ya están descontados/);
    assert.doesNotMatch(html, /<img|<script/);
    assert.match(html, /&lt;img/);
    filters = {cutoff_date: '2026-10-31'};
    const loading = dialog.options.secondary_action();
    assert.equal(JSON.parse(requests[1].options.args.filters).cutoff_date, '2026-08-31', 'Refresh keeps the original cutoff');
    dialog.hide();
    const afterClose = html;
    requests[1].resolve({message: {client_name: 'STALE'}});
    await loading;
    assert.equal(html, afterClose, 'No late rendering into a closed modal');
    console.log('OK: clickable assigned amounts, cutoff, links, safe HTML, refresh and stale responses.');
})().catch(error => {console.error(error); process.exitCode = 1;});
