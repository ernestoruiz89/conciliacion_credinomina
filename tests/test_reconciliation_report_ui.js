const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({frappe: {query_reports: {}, utils: {
    escape_html: text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
}}, __: text => text});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/report/resumen_de_conciliacion/resumen_de_conciliacion.js'), 'utf8'), context);
const report = context.frappe.query_reports['Resumen de Conciliacion'];
assert.equal(report.filters.find(f => f.fieldname === 'view').default, 'Resumen');
assert.ok(report.filters.find(f => f.fieldname === 'period'));
const render = (field, value, extra = {}) => report.formatter(value, 0, {fieldname: field},
    {[field]: value, ...extra}, value => String(value));
assert.ok(render('collection_usd', null, {collection_control: 'No aplica'}).includes('No aplica'));
assert.ok(render('deposit_usd', null).includes('No determinable'));
assert.ok(render('collection_difference_usd', 10).includes('text-danger'));
assert.ok(render('deposit_difference_usd', -10).includes('text-warning'));
assert.equal(render('open_usd', 0), '0');
// Free-text comments and links still go through Frappe's default formatter.
for (const field of ['first_exception_comment', 'portfolio_credit_status']) {
    let called = false;
    const formatted = report.formatter('<script>', 0, {fieldname: field}, {}, () => {called = true; return '&lt;script&gt;';});
    assert.ok(called);
    assert.equal(formatted, '&lt;script&gt;');
}
assert.equal(render('comments', ''), '');
assert.equal(render('comments', null), '');
const comments = render('comments', 'Comentarios: <script>\nAplicación: A & B\nDiferencias CXC: Revisar');
assert.ok(comments.includes('white-space:pre-wrap'));
assert.ok(comments.includes('&lt;script&gt;'));
assert.ok(comments.includes('Aplicación: A &amp; B\nDiferencias CXC: Revisar'));
assert.ok(!comments.includes('<script>'));
assert.equal(report.get_datatable_options({cellHeight: 33}).dynamicRowHeight, true);
assert.equal(report.get_datatable_options({cellHeight: 33}).cellHeight, 33);
console.log('Reconciliation report UI: passed');
