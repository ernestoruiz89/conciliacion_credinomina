const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const translate = (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]);
const esc = value => String(value).replace(/[&<>"']/g, char =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[char]);
const requests = [];
const context = {__: translate, format_currency: value => `US$ ${Number(value).toFixed(2)}`,
    $: value => value, frappe: {ui: {form: {on() {}}}, utils: {escape_html: esc},
        call: args => new Promise((resolve, reject) => requests.push({args, resolve, reject}))}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_accounting_import/cn_accounting_import.js', 'utf8'), context);
const source = {name: 'APP', idx: 1, source_row: 5100, client_name: '<unsafe>', client_number: '3973', loan_number: '0011685-1',
    event_type: 'Aplicacion', effective: 1, match_status: 'Conciliado', historical_period: 'MIDESA-11-2025-04',
    deposit_match_status: 'Depósito parcial', deposit_match_reason: 'Aplicación con US$ 26.32 pendientes de evidencia de depósito.',
    remittance_allocation: 'DEP-1', complementary_item: 'CN-COMP-1',
    application_allocation_detail: '[{"period":"MIDESA-11-2025-04"}]',
    historical_detail: '[{"referencia":"BANK-REF","comprobante":"VOUCHER","importe_usd":10}]'};
const doc = {name: 'IMPORT', employer: 'MIDESA', status: 'Importado con excepciones', rows: [source]};
const before = JSON.stringify(doc);
const html = context.accountingImportIssuesHtml(doc);
assert.match(html, /Qué falta para conciliar/);
assert.match(html, /Fila del archivo.*5100/);
assert.match(html, /&lt;unsafe&gt;/);
assert.ok(!html.includes('<unsafe>'));
assert.match(html, /cn-remittance-allocation\/DEP-1/);
assert.match(html, /cn-complementary-item\/CN-COMP-1/);
assert.match(html, /cn-reconciliation-period\/MIDESA-11-2025-04/);
assert.match(html, /26.32 pendientes/);
assert.match(html, /Complete la evidencia del importe pendiente/);
assert.match(html, /data-cn-import-issue-row="APP"/);
assert.ok(!html.includes('/BANK-REF'), 'bank references must never become invented deposit links');
assert.equal(JSON.stringify(doc), before, 'diagnostics cannot mutate saved rows or amounts');
const records = context.accountingImportIssueRecords(source, doc);
assert.equal(records.filter(record => record.name === 'MIDESA-11-2025-04').length, 1);
const unknown = {...source, name:'B', remittance_allocation:'', complementary_item:'', historical_period:'',
    application_allocation_detail:'malformed', historical_detail:'{}', deposit_match_status:'Sin deposito'};
assert.match(context.accountingImportIssuesHtml({...doc, rows:[unknown]}), /Sin registro vinculado/);
assert.match(context.accountingImportIssuesHtml({...doc, rows:[unknown]}), /Depósito: por identificar/);
assert.equal(context.accountingImportIssueRecords({...unknown, allocation_detail:'[{"partida":{"bad":true}}]'}, {}).length, 0);
const settled = {...source, deposit_match_status: 'Depósito conciliado'};
assert.equal(context.accountingImportIssuesHtml({...doc, status:'Importado', rows:[settled]}), '');
assert.equal(context.accountingImportIssuesHtml({...doc, docstatus:2}), '');
assert.match(context.accountingImportIssuesHtml(doc, true), /cambios sin guardar/);
const unlinked = {...unknown, match_status:'Sin coincidencia', match_reason:'Falta crédito del movimiento.', loan_number:''};
assert.match(context.accountingImportIssuesHtml({...doc, rows:[unlinked]}), /Nro. Crédito de esta fila/);
assert.match(context.accountingImportIssuesHtml({...doc, employer:'', rows:[unlinked]}), /Revisar empresa/);
const noPeriod = {...unlinked, loan_number:'L', match_reason:'Asigne el período de la aplicación.'};
assert.match(context.accountingImportIssuesHtml({...doc, rows:[noPeriod]}), /Revisar período/);
const large = {...doc, rows:Array.from({length:120}, (_, i) => ({...source, idx:i+1, name:`A${i}`, client_name:`Persona ${i}`}))};
const largeHtml = context.accountingImportIssuesHtml(large);
assert.equal((largeHtml.match(/data-cn-import-issue-row=/g) || []).length, 25);
assert.match(largeHtml, /primeras 25 de 120/);
assert.match(largeHtml, /Ver todos los pendientes detectados/);

function wrapper() {
    return {content:'', handlers:new Map(), html(value) {this.content = value; return this;},
        off(namespace) {for (const key of this.handlers.keys()) if (key.includes(namespace)) this.handlers.delete(key); return this;},
        on(event, selector, callback) {this.handlers.set(`${event}|${selector}`, callback); return this;},
        find() {return {html() {}, text() {}, prop() {}};}};
}
(async () => {
    const balance = wrapper(), clients = wrapper(), visited = [], opened = [];
    const frm = {doc, is_new:()=>false, is_dirty:()=>false, scroll_to_field:async field => visited.push(field),
        fields_dict:{balance_summary_html:{$wrapper:balance}, client_summary_html:{$wrapper:clients},
            rows:{grid:{grid_rows_by_docname:{APP:{toggle_view:value => opened.push(value)}}}}}};
    const load = context.loadAccountingClientSummary(frm);
    assert.match(balance.content, /Qué falta para conciliar/, 'blockers must be visible while balances are loading');
    requests.shift().resolve({message:{rows:[], totals:{amount_usd:100, balance_usd:26.32}}});
    await load;
    assert.match(balance.content, /Importe US\$/);
    assert.match(balance.content, /Qué falta para conciliar/, 'totals must not overwrite diagnostics');
    context.bindAccountingImportIssues(frm);
    assert.equal(balance.handlers.size, 3, 'refresh must replace event handlers');
    const action = balance.handlers.get('click.cnImportIssues|[data-cn-import-issue-row]');
    await action({currentTarget:{dataset:{cnImportIssueRow:'APP', cnImportIssueField:'rows'}}});
    assert.deepEqual(visited, ['rows']); assert.deepEqual(opened, [true]);
    await action({currentTarget:{dataset:{cnImportIssueField:'historical_period'}}});
    assert.equal(visited.at(-1), 'historical_period');
    let modal;
    context.showImportExceptions = form => {modal = form;};
    balance.handlers.get('click.cnImportIssues|[data-cn-import-all-issues]')();
    assert.equal(modal, frm);
    const old = context.loadAccountingClientSummary(frm);
    frm.doc = {...doc, name:'OTHER', rows:[{...source, client_name:'Cliente nuevo'}]};
    const current = context.loadAccountingClientSummary(frm);
    const oldRequest = requests.shift(), currentRequest = requests.shift();
    currentRequest.resolve({message:{rows:[], totals:{}}}); await current;
    const newContent = balance.content;
    oldRequest.resolve({message:{rows:[], totals:{amount_usd:999999}}}); await old;
    assert.equal(balance.content, newContent, 'stale balances cannot overwrite the current import blockers');
    const failed = context.loadAccountingClientSummary(frm);
    requests.shift().reject(new Error('Unavailable totals')); await failed;
    assert.match(balance.content, /No se pudieron consultar los saldos/);
    assert.match(balance.content, /Cliente nuevo/);
    assert.match(balance.content, /Qué falta para conciliar/, 'a balance failure must preserve known diagnostics');
    console.log('OK: import blockers, row/source numbers, saved links, corrections, navigation, unknowns, bounded table, escaping and stale requests.');
})().catch(error => {console.error(error); process.exitCode = 1;});
