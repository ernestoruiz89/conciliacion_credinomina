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
    return footers;
};
const value = footer => /class="text-right cn-related-pending-total"[^>]*>([^<]*)<\/td>/.exec(footer)[1];
let footers = show({applications:[
    {type:'Aplicación', document:'A', amount_usd:100, pending_usd:12.34,
        deposits:[{document:'PAID', amount_usd:87.66}]},
    {type:'Aplicación', document:'B', amount_usd:80, pending_usd:7.66},
],rows:[]});
assert.equal(value(footers[0]), '20.00', 'Use pending balances, not original application amounts or nested paid deposits');
assert.equal(value(footers[1]), '0.00');
footers = show({applications:[],rows:[
    {type:'Depósito', document:'DEP', amount_usd:500, pending_usd:40},
    {type:'Partida complementaria', document:'CREDIT', direction:'Crédito', amount_usd:50, pending_usd:5},
    {type:'Partida complementaria', document:'DEBIT', direction:'Débito', amount_usd:130, pending_usd:13},
]});
assert.equal(value(footers[1]), '32.00', 'Pending deposits + credits − debits, regardless of gross amount');
assert.ok(html.includes('Total pendiente neto = depósitos + partidas de crédito − partidas de débito'));
footers = show({applications:[],rows:[
    {type:'Depósito', pending_usd:10}, {type:'Partida complementaria', direction:'Débito', pending_usd:15},
]});
assert.equal(value(footers[1]), '-5.00', 'Keep the sign when pending debits exceed receipts');
for (const row of [
    {type:'Partida complementaria', pending_usd:5},
    {type:'Partida complementaria', direction:'Crédito', pending_usd:null},
    {type:'Partida complementaria', direction:'Débito', pending_usd:-3},
]) {
    footers = show({applications:[],rows:[{type:'Depósito', pending_usd:10},row]});
    assert.equal(value(footers[1]), '—', 'Do not invent a balance for an unknown direction, unknown amount or overassignment');
}
footers = show({applications:[{type:'Aplicación', pending_usd:0.1},{type:'Aplicación', pending_usd:0.2}], rows:[]});
assert.equal(value(footers[0]), '0.30', 'Totals are accumulated in cents');
for (const unknown of [null, undefined, '', 'bad', Infinity, -0.01]) {
    footers = show({applications:[{type:'Aplicación', pending_usd:2},{type:'Aplicación', pending_usd:unknown}], rows:[]});
    assert.equal(value(footers[0]), '—');
    assert.ok(footers[0].includes('Total no determinable'));
}
footers = show({applications:[],rows:[]});
assert.equal(value(footers[0]), '0.00');
assert.equal(value(footers[1]), '0.00');
console.log('OK: pending totals, signed debit/credit net, cents, unknown balances and read-only rendering.');
