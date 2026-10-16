const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const context = vm.createContext({__: x => x, $: node => ({attr: key => node[key]}), frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_por_empresa/estado_de_cuenta_por_empresa.js'), 'utf8'), context);
const report = context.frappe.query_reports['Estado de Cuenta por Empresa'];
context.frappe.utils = {escape_html: text => text};
assert.equal(report.filters[0].default, 'Resumen');
assert.equal(report.filters[0].options, 'Resumen\nDetalle');
assert.ok(report.filters.some(f => f.fieldname === 'employer' && f.options === 'CN Employer'));
assert.ok(report.filters.some(f => f.fieldname === 'cutoff_date' && f.fieldtype === 'Date' && !f.reqd));
assert.ok(report.filters.some(f => f.fieldname === 'accounting_queue' && f.fieldtype === 'Check'));
assert.ok(report.filters.some(f => f.fieldname === 'origin_type'));
const col = {fieldname: 'balance_usd', fieldtype: 'Currency'}, base = v => `formatted:${v}`;
assert.match(report.formatter(null, null, col, {balance_usd: null}, base), /—/);
assert.equal(report.formatter(0, null, col, {balance_usd: 0}, base), 'formatted:0');
assert.equal(report.formatter(-7.35, null, col, {balance_usd: -7.35}, base), 'formatted:-7.35');
let drill, back;
const filterValues = Object.fromEntries(report.filters.map(filter => [filter.fieldname, filter.default ?? '']));
Object.assign(filterValues, {from_date:'2025-04-01', cutoff_date:'2025-06-30', search:'Ana'});
const original = {...filterValues};
const live = {get_filter_value: key => filterValues[key], set_filter_value: values => Object.assign(filterValues, values),
    get_filter: () => ({get_value: () => '', $wrapper:{toggle(){}}}),
    page: {wrapper:{off(){}, on(event, selector, handler){drill = handler;}},
        add_inner_button(label, handler){if(label === 'Volver al resumen') back = handler; return {text(){}};}}};
report.onload(live);
drill.call({'data-detail':JSON.stringify({employer:'Gamma',component:'application_pending_usd'})}, {preventDefault(){}});
assert.equal(filterValues.employer, 'Gamma');
assert.equal(filterValues.view_mode, 'Detalle');
Object.assign(filterValues, {search:'Otra búsqueda', status:'Pendiente'});
back();
assert.deepEqual(filterValues, original, 'Returning restores every filter, including the all-company scope and empty filters');
Object.assign(filterValues, {employer:'Alfa', component:'core_pending_usd'});
const scoped = {...filterValues};
drill.call({'data-detail':JSON.stringify({employer:'Alfa',component:'core_pending_usd'})}, {preventDefault(){}});
back();
assert.deepEqual(filterValues, scoped, 'A summary initially filtered to one company remains scoped');
const queueLink = report.formatter(2, null, {fieldname:'accounting_pending_count', fieldtype:'Int'},
    {employer:'Alfa', accounting_pending_count:2}, base);
assert.match(queueLink, /cn-statement-detail/);
assert.match(queueLink, /accounting_queue/);
drill.call({'data-detail':JSON.stringify({employer:'Alfa', component:'', accounting_queue:1})}, {preventDefault(){}});
assert.equal(filterValues.accounting_queue, 1);
assert.equal(filterValues.component, '');
back();
assert.deepEqual(filterValues, scoped, 'The accounting queue drill restores the original summary filters');
console.log('OK: company statement summary/detail, monetary formatting and original filter restoration.');
