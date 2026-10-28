const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let html = '', denied = false;
const esc = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
const entry = {kind: 'Saldo a favor contra depósito', parent_document: 'DEP-11-2025-0021',
    employer: 'MULTIPERFILES', client_name: 'SIRIO <x>', loan_number: '109587-1', amount_usd: 25.22,
    effective_date: '2025-12-31', management_history: [{importe_usd: 5}]};
const application = {kind: 'Pago pendiente contra aplicación', parent_document: 'CONTA-1',
    employer: 'MULTIPERFILES', client_name: 'OTRO CLIENTE', loan_number: '123-1', amount_usd: 1,
    effective_date: '2025-12-31'};
const ctx = vm.createContext({__: value => value, format_currency: value => `US$ ${Number(value).toFixed(2)}`,
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: esc},
        call: async () => {if (denied) throw Error('restringido'); return {message: {entries: [entry, application]}};}}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), ctx);
const wrapper = {html(value) {html = value;}, empty() {html = '';}};
const frm = {doc: {name: 'CN-COMP-2026-00515', __onload: {has_reclassification_distributions: true}},
    is_new: () => false, fields_dict: {reclassification_details: {$wrapper: wrapper}}};
(async () => {
    const removed = [];
    const settled = {doc: {docstatus: 1, category: 'Compensación entre partidas', pending_usd: 0}, remove_custom_button: label => removed.push(label)};
    assert.equal(ctx.cn_has_available_balance(settled), false);
    ctx.cn_hide_exhausted_actions(settled, {pending_usd: 0});
    assert.ok(removed.includes('Distribuir a Depósito') && removed.includes('Compensar con otra partida'));
    assert.ok(!removed.includes('Eliminar distribución'));
    assert.equal(ctx.cn_has_available_balance({...settled, doc: {...settled.doc, pending_usd: .01}}), true);
    await ctx.cn_render_reclassification_details(frm);
    assert.match(html, /DEP-11-2025-0021/);
    assert.match(html, /cn-remittance-allocation\/DEP-11-2025-0021/);
    assert.match(html, /109587-1/);
    assert.match(html, /Saldo a favor del cliente/);
    assert.match(html, /US\$ 25\.22/);
    assert.match(html, /US\$ 20\.22/);
    assert.match(html, /cn-accounting-import\/CONTA-1/);
    assert.match(html, /Pago pendiente contra aplicación/);
    assert.match(html, /SIRIO &lt;x&gt;/);
    denied = true;
    await ctx.cn_render_reclassification_details(frm);
    assert.match(html, /acceso a sus documentos/);
    assert.doesNotMatch(html, /DEP-11-2025-0021/);
    frm.doc.__onload.has_reclassification_distributions = false;
    await ctx.cn_render_reclassification_details(frm);
    assert.equal(html, '');
    console.log('OK: authorized item links and separate management balances.');
})().catch(error => {console.error(error); process.exitCode = 1;});
