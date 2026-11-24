const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]),
    frappe: {ui: {form: {on() {}}}, utils: {escape_html: value => String(value).replace(/[&<>"']/g,
        char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[char])}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js', 'utf8'), context);
const doc = {docstatus: 1, result: 'Revisar detalle', detail_status: 'Revisar filas',
    amount_usd: 773.36, allocated_usd: 773.36, unallocated_usd: 0, unclassified_usd: 0,
    detail_rows: [{source_row: 2, client_name: '<unsafe>', loan_number: '0011685-1', match_status: 'Revisar',
        match_reason: 'Partida CN-COMP-2026-00502: Falta el número de crédito; la fila requiere 0011685-1.'}],
    targets: [{complementary_item: 'CN-COMP-2026-00502', result: 'Aplicada'}]};
const issues = context.remittanceIssues(doc);
assert.equal(issues.length, 1, 'fully distributed cash still needs its failed detail reason');
assert.match(issues[0].text, /Fila 2.*CN-COMP-2026-00502.*0011685-1/);
const html = context.remittanceIssuesHtml(doc, false, true);
assert.match(html, /Qué falta para conciliar/);
assert.match(html, /&lt;unsafe&gt;/);
assert.ok(!html.includes('<unsafe>'));
assert.match(html, /data-cn-issue-field="detail_rows"/);
const blocked = {docstatus: 1, name: 'DEP-10-2026-0057', employer: 'MAYACERO',
    result: 'Revisar destinos', detail_status: 'Revisar filas', amount_usd: 59.26,
    allocated_usd: 0, unclassified_usd: 59.26,
    detail_rows: [10, 11].map((source_row, index) => ({name: `R${source_row}`, source_row,
        row_kind: 'Cliente', employer: 'MULTIPERFILES', client_name: 'LUIS FRANCISCO RAMIREZ PACHECO',
        loan_number: '109673-1', amount_usd: 29.63, deducted_usd: 29.63,
        match_status: 'Revisar', match_reason: 'Conflicto: el crédito no tiene evidencia en la empresa de la fila',
        matched_targets: '[]', pending_usd: 0})),
    targets: [10, 11].map((source_row, index) => ({detail_row: `R${source_row}`,
        historical_application: `APP-${index}`, amount_usd: 29.63,
        notes: `MULTIPERFILES · LUIS FRANCISCO RAMIREZ PACHECO · Crédito 13816-1`,
        result: 'Conflicto: el crédito no tiene evidencia en la empresa de la fila'}))};
const blockedIssues = context.remittanceIssues(blocked);
assert.equal(blockedIssues.filter(issue => issue.text.includes('Vincular crédito desde cartera')).length, 2);
assert.match(blockedIssues[0].text, /Fila 10.*MULTIPERFILES.*109673-1.*29\.63.*no tiene evidencia.*destino seleccionado indica el crédito 13816-1/);
assert.ok(blockedIssues.some(issue => /Destino.*13816-1/.test(issue.text)));
assert.ok(blockedIssues.some(issue => /59\.26 sin distribuir/.test(issue.text)));
const summary = context.remittanceReconciliationSummaryHtml(blocked, {deposit: blocked.name});
assert.match(summary, /Qué falta para conciliar/);
assert.match(summary, /Vincular crédito desde cartera/);
let overview = '', scrolled = '';
const handlers = {};
const wrapper = {find: () => ({length: 1, html: value => {overview = value;}}),
    off() {return this;}, on(event, selector, handler) {handlers[selector] = handler; return this;}};
context.renderRemittanceOverview({doc: blocked, $wrapper: wrapper, dashboard: {show() {}}, fields_dict: {},
    get_perm: () => true, is_dirty: () => false, is_new: () => false,
    scroll_to_field: field => {scrolled = field;}});
assert.match(overview, /Qué falta para conciliar/);
assert.match(overview, /Fila 11.*MULTIPERFILES.*109673-1/);
handlers['[data-cn-issue-field]'].call({getAttribute: () => 'detail_rows'});
assert.equal(scrolled, 'detail_rows');
const difference = {docstatus: 1, result: 'Parcial', detail_status: 'Parcial; diferencia de empresa pendiente',
    amount_usd: 1, allocated_usd: 1, detail_rows: [{row_kind: 'Diferencia documentada',
        difference_amount_usd: -.02, amount_usd: -.02, difference_core_status: 'Pendiente de respaldo del core',
        match_status: 'Revisar: Diferencia documentada'}]};
assert.match(context.remittanceIssues(difference)[0].text, /0\.02.*pendiente de respaldo del core/);
difference.result = 'Conciliado con diferencia documentada'; difference.detail_status = difference.result;
assert.equal(context.remittanceIssuesHtml(difference), '', 'accepted company difference is not a blocker');
doc.detail_rows[0].match_status = 'Conciliada'; doc.result = 'Conciliado'; doc.detail_status = 'Conciliado';
assert.equal(context.remittanceIssuesHtml(doc), '', 'settled deposit must have no blockers');
doc.apply_fifo = 1; doc.detail_periods = [];
assert.match(context.remittanceIssues(doc)[0].text, /FIFO.*período/);
assert.match(context.remittanceIssues(doc, true)[0].text, /sin guardar/);
doc.docstatus = 2;
assert.equal(context.remittanceIssuesHtml(doc), '');
console.log('OK: precise saved blockers, fully assigned cash, escaping, FIFO, dirty and cancelled states.');
