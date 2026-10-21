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
doc.detail_rows[0].match_status = 'Conciliada'; doc.result = 'Conciliado'; doc.detail_status = 'Conciliado';
assert.equal(context.remittanceIssuesHtml(doc), '', 'settled deposit must have no blockers');
doc.apply_fifo = 1; doc.detail_periods = [];
assert.match(context.remittanceIssues(doc)[0].text, /FIFO.*período/);
assert.match(context.remittanceIssues(doc, true)[0].text, /sin guardar/);
doc.docstatus = 2;
assert.equal(context.remittanceIssuesHtml(doc), '');
console.log('OK: precise saved blockers, fully assigned cash, escaping, FIFO, dirty and cancelled states.');
