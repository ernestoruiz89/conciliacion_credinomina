const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
let html;
const context = vm.createContext({window:{}, __:text => text, format_currency:value => Number(value).toFixed(2), frappe:{
    utils:{escape_html:value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')},
    ui:{Dialog:function() {
        this.fields_dict = {related_html:{$wrapper:{html(value) {html = value;}}}};
        this.show = () => {};
    }},
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/public/js/related_pending_modal.js','utf8'), context);
const show = data => {
    const before = JSON.stringify(data);
    context.window.credinominaShowRelatedPending(data);
    assert.equal(JSON.stringify(data), before, 'Displaying totals must not rewrite pending amounts or assignments');
    const footers = [...html.matchAll(/<tfoot>([\s\S]*?)<\/tfoot>/g)].map(match => match[1]);
    assert.equal(footers.length, 2, 'Each pending table has its own footer');
    const summaryStart = html.indexOf('class="cn-related-pending-summary"');
    const tablesStart = html.indexOf('<table');
    assert.ok(summaryStart >= 0 && summaryStart < tablesStart, 'Totals must be visible above the scrollable tables');
    for (const [index, suffix] of ['applications', 'sources'].entries()) {
        const match = new RegExp('class="cn-related-pending-summary-' + suffix + '"[^>]*>([^<]*)</strong>').exec(html);
        assert.equal(match[1], value(footers[index]), 'Summary and footer must display the same pending total');
        assert.ok(match.index < tablesStart, 'Both totals must appear before the table scroll areas');
    }
    assert.equal((html.match(/class="cn-related-pending-difference-value"/g) || []).length, 1);
    assert.ok(html.indexOf('class="cn-related-pending-difference-value"') < tablesStart);
    return footers;
};
const value = footer => /class="text-right cn-related-pending-total"[^>]*>([^<]*)<\/td>/.exec(footer)[1];
const difference = () => /class="cn-related-pending-difference-value"[^>]*>([^<]*)<\/strong>/.exec(html)[1];
let footers = show({applications:[
    {type:'Aplicación', document:'A', amount_usd:100, pending_usd:12.34,
        deposits:[{document:'PAID', amount_usd:87.66}]},
    {type:'Aplicación', document:'B', amount_usd:80, pending_usd:7.66},
],rows:[]});
assert.equal(value(footers[0]), '20.00', 'Use pending balances, not original application amounts or nested paid deposits');
assert.equal(value(footers[1]), '0.00');
assert.equal(difference(), '20.00');
assert.ok(html.includes('Las aplicaciones pendientes superan el total neto mostrado.'));
footers = show({applications:[{type:'Aplicación', pending_usd:20}],rows:[
    {type:'Depósito', document:'DEP', amount_usd:500, pending_usd:40},
    {type:'Partida complementaria', document:'CREDIT', direction:'Crédito', amount_usd:50, pending_usd:5},
    {type:'Partida complementaria', document:'DEBIT', direction:'Débito', amount_usd:130, pending_usd:13},
]});
assert.equal(value(footers[1]), '32.00', 'Pending deposits + credits − debits, regardless of gross amount');
assert.equal(difference(), '-12.00', 'Compare application pending against the same signed net shown in the footer');
assert.ok(html.includes('El total neto mostrado supera las aplicaciones pendientes.'));
assert.ok(html.includes('Aplicaciones pendientes − total neto de depósitos y partidas complementarias.'));
assert.ok(html.includes('Total pendiente neto = depósitos + partidas de crédito − partidas de débito'));
footers = show({applications:[],rows:[
    {type:'Depósito', pending_usd:10}, {type:'Partida complementaria', direction:'Débito', pending_usd:15},
]});
assert.equal(value(footers[1]), '-5.00', 'Keep the sign when pending debits exceed receipts');
assert.equal(difference(), '5.00');
for (const row of [
    {type:'Partida complementaria', pending_usd:5},
    {type:'Partida complementaria', direction:'Crédito', pending_usd:null},
    {type:'Partida complementaria', direction:'Débito', pending_usd:-3},
]) {
    footers = show({applications:[],rows:[{type:'Depósito', pending_usd:10},row]});
    assert.equal(value(footers[1]), '—', 'Do not invent a balance for an unknown direction, unknown amount or overassignment');
    assert.equal(difference(), '—', 'An undetermined net makes the difference undetermined too');
}
footers = show({applications:[{type:'Aplicación', pending_usd:0.1},{type:'Aplicación', pending_usd:0.2}], rows:[]});
assert.equal(value(footers[0]), '0.30', 'Totals are accumulated in cents');
assert.equal(difference(), '0.30');
show({applications:[{type:'Aplicación', pending_usd:0.1},{type:'Aplicación', pending_usd:0.2}],
    rows:[{type:'Depósito', pending_usd:0.3}]});
assert.equal(difference(), '0.00', 'Equal cent totals must not create a floating-point difference');
assert.ok(html.includes('Los totales coinciden.'));
for (const unknown of [null, undefined, '', 'bad', Infinity, -0.01]) {
    footers = show({applications:[{type:'Aplicación', pending_usd:2},{type:'Aplicación', pending_usd:unknown}], rows:[]});
    assert.equal(value(footers[0]), '—');
    assert.ok(footers[0].includes('Total no determinable'));
    assert.equal(difference(), '—');
}
footers = show({applications:[],rows:[]});
assert.equal(value(footers[0]), '0.00');
assert.equal(value(footers[1]), '0.00');
assert.equal(difference(), '0.00');
footers = show({applications: Array.from({length:5}, () => ({type:'Aplicación', pending_usd:24.69})),
    rows:[44.79, 0.01, 0.01, 0.01, 0.01, 22.40].map(pending_usd => ({type:'Depósito', pending_usd}))});
assert.equal(value(footers[0]), '123.45');
assert.equal(value(footers[1]), '67.23');
assert.equal(difference(), '56.22');
console.log('OK: pending totals, signed net difference, cents, unknown balances and read-only rendering.');
