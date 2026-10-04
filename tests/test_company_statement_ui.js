const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const context = vm.createContext({__: x => x, frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_por_empresa/estado_de_cuenta_por_empresa.js'), 'utf8'), context);
const report = context.frappe.query_reports['Estado de Cuenta por Empresa'];
assert.equal(report.filters[0].default, 'Resumen');
assert.equal(report.filters[0].options, 'Resumen\nDetalle');
assert.ok(report.filters.some(f => f.fieldname === 'employer' && f.options === 'CN Employer'));
const col = {fieldname: 'balance_usd', fieldtype: 'Currency'}, base = v => `formatted:${v}`;
assert.match(report.formatter(null, null, col, {balance_usd: null}, base), /—/);
assert.equal(report.formatter(0, null, col, {balance_usd: 0}, base), 'formatted:0');
assert.equal(report.formatter(-7.35, null, col, {balance_usd: -7.35}, base), 'formatted:-7.35');
console.log('OK: company statement summary/detail and monetary formatting.');
