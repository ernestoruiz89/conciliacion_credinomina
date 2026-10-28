const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const translate = (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]);
const esc = value => String(value).replace(/[&<>"']/g, char =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[char]);
const requests = [];
const dialogs = [], handlers = {};
const context = {__: translate, format_currency: value => `US$ ${Number(value).toFixed(2)}`,
    $: value => value, frappe: {ui: {form: {on(doctype, events) {handlers[doctype] = events;}}, Dialog: class {
        constructor(options) {this.options = options; this.wrapper = wrapper(); dialogs.push(this);}
        get_field() {return {$wrapper: this.wrapper};}
        show() {this.visible = true;} hide() {this.visible = false;}
    }}, utils: {escape_html: esc},
        format: (value, df, options, doc) => df.formatter ? df.formatter(value, df, options, doc)
            : `${doc.usd_currency} ${Number(value).toFixed(df.precision)}`,
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
const financialDoc = value => JSON.stringify(value, (key, item) =>
    ['_cn_row_amounts', 'row_applied_usd', 'row_balance_usd'].includes(key) ? undefined : item);
const html = context.accountingImportIssuesHtml(doc);
assert.match(html, /Pendientes de conciliación/);
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
assert.match(largeHtml, /Mostrando 1–25 de 120/);
assert.match(largeHtml, /Filtrar por estado, etapa o importe/);
assert.match(context.accountingImportIssuesHtml(large, false, {page: 1}), /data-cn-import-issue-row="A25"/);
assert.equal((context.accountingImportIssuesHtml(large, false, {query:'Persona 119'}).match(/data-cn-import-issue-row=/g) || []).length, 1);

function wrapper() {
    return {content:'', handlers:new Map(), children:new Map(), html(value) {this.content = value; return this;},
        off(namespace) {for (const key of this.handlers.keys()) if (key.includes(namespace)) this.handlers.delete(key); return this;},
        on(event, selector, callback) {this.handlers.set(`${event}|${selector}`, callback); return this;},
        text() {return this;}, prop() {return this;},
        find(selector) {if (!this.children.has(selector)) this.children.set(selector, wrapper()); return this.children.get(selector);}};
}
(async () => {
    const balance = wrapper(), visited = [], opened = [], formatters = {}, refreshed = [];
    const frm = {doc, is_new:()=>false, is_dirty:()=>false, scroll_to_field:async field => visited.push(field),
        fields_dict:{balance_summary_html:{$wrapper:balance},
            rows:{grid:{grid_rows_by_docname:{APP:{toggle_view:value => opened.push(value), refresh_field:field => refreshed.push(field)}},
                refresh() {assert.fail('updating the monetary cells must not redraw the grid');},
                update_docfield_property(field, property, formatter) {assert.equal(property, 'formatter'); formatters[field] = formatter;}}}}};
    context.setupAccountingRowAmounts(frm);
    assert.match(formatters.row_applied_usd(0, {}, {}, source), /—/, 'an unloaded value is not zero');
    const load = context.loadAccountingClientSummary(frm);
    assert.match(balance.content, /Consultando saldos/);
    requests.shift().resolve({message:{rows:[], totals:{amount_usd:100, balance_usd:26.32},
        row_amounts:{APP:{applied_usd:73.68, balance_usd:26.32, issue:''}}}});
    await load;
    assert.match(balance.content, /Importe US\$/);
    assert.ok(!balance.content.includes('<table'), 'only summary cards are embedded above Filas');
    assert.equal(source.row_applied_usd, 73.68);
    assert.equal(source.row_balance_usd, 26.32);
    assert.ok(refreshed.includes('row_applied_usd') && refreshed.includes('row_balance_usd'));
    assert.equal(formatters.row_applied_usd(0, {}, {}, source), 'USD 73.68');
    assert.equal(formatters.row_balance_usd(0, {}, {}, source), 'USD 26.32');
    assert.equal(context.frappe.format(0, {formatter: formatters.row_balance_usd}, {}, source), 'USD 26.32',
        'opening the accounting row must not recursively dispatch its own formatter');
    const zero = {_cn_row_amounts:{applied_usd:0, balance_usd:0, issue:'<review>'}};
    assert.match(formatters.row_applied_usd(0, {}, {}, zero), /USD 0.00/);
    assert.match(formatters.row_balance_usd(0, {}, {}, zero), /&lt;review&gt;/);
    const unknownBalance = {_cn_row_amounts:{applied_usd:null, balance_usd:null}};
    assert.match(formatters.row_balance_usd(0, {}, {}, unknownBalance), /—/);
    assert.equal(financialDoc(frm.doc), before, 'only virtual display values can change');
    assert.equal(dialogs.length, 0, 'summaries must not open automatically');
    context.showAccountingImportIssues(frm);
    let pending = dialogs.at(-1);
    assert.equal(pending.options.title, 'Pendientes de conciliación');
    assert.match(pending.wrapper.find('.cn-import-issues-content').content, /26.32 pendientes/);
    const handlerCount = pending.wrapper.handlers.size;
    context.renderAccountingImportIssues(frm, pending.wrapper, () => pending.hide());
    assert.equal(pending.wrapper.handlers.size, handlerCount, 'redraw replaces event handlers');
    const action = pending.wrapper.handlers.get('click.cnImportIssues|[data-cn-import-issue-row]');
    await action({currentTarget:{dataset:{cnImportIssueRow:'APP', cnImportIssueField:'rows'}}});
    assert.equal(pending.visible, false, 'close the diagnostic modal before opening the original row');
    assert.deepEqual(visited, ['rows']); assert.deepEqual(opened, [true]);
    await action({currentTarget:{dataset:{cnImportIssueField:'historical_period'}}});
    assert.equal(visited.at(-1), 'historical_period');
    let modal;
    context.showImportExceptions = form => {modal = form;};
    pending.wrapper.handlers.get('click.cnImportIssues|[data-cn-import-all-issues]')();
    assert.equal(modal, frm);

    const clientLoad = context.showAccountingClientSummary(frm), clients = dialogs.at(-1);
    assert.equal(clients.options.title, 'Saldos por cliente');
    assert.match(clients.wrapper.content, /Consultando saldos por cliente/);
    requests.shift().resolve({message:{rows:[{client_name:'Cliente 1', applied_usd:100, assigned_usd:73.68,
        rounding_usd:0, balance_usd:26.32, status:'Parcial'}], totals:{amount_usd:100, balance_usd:26.32}}});
    await clientLoad;
    assert.match(clients.wrapper.find('.cn-client-table').content, /Cliente 1/);
    assert.ok(!balance.content.includes('<table'), 'opening a summary does not add another table to the form');
    assert.equal(financialDoc(frm.doc), before, 'consultations cannot alter the import');
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
    assert.equal(frm.doc.rows[0].row_balance_usd, null, 'failed reads must not leave a stale zero');
    assert.match(balance.content, /Reintentar/);
    context.showAccountingImportIssues(frm);
    pending = dialogs.at(-1);
    assert.match(pending.wrapper.find('.cn-import-issues-content').content, /Cliente nuevo/,
        'saved diagnostics remain accessible when the financial query fails');

    let message;
    context.frappe.msgprint = text => {message = text;};
    frm.is_dirty = () => true;
    const count = dialogs.length;
    context.showAccountingClientSummary(frm);
    assert.equal(dialogs.length, count);
    assert.match(message, /Guarde la importación/);
    context.showAccountingImportIssues(frm);
    assert.match(dialogs.at(-1).wrapper.find('.cn-import-issues-content').content, /cambios sin guardar/);
    console.log('OK: one embedded grid, optional client/pending modals, row navigation, saved data, filters, retry and stale responses.');
})().catch(error => {console.error(error); process.exitCode = 1;});
