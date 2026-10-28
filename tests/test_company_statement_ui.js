const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const context = vm.createContext({__: x => x, $: node => ({attr: key => node[key]}), frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_por_empresa/estado_de_cuenta_por_empresa.js'), 'utf8'), context);
const report = context.frappe.query_reports['Estado de Cuenta por Empresa'];
context.frappe.utils = {escape_html: text => text};
assert.equal(report.filters[0].default, 'Resumen');
assert.equal(report.filters[0].options, 'Resumen\nDetalle\nConciliación');
assert.ok(report.filters.some(f => f.fieldname === 'employer' && f.options === 'CN Employer'));
assert.ok(report.filters.some(f => f.fieldname === 'cutoff_date' && f.fieldtype === 'Date' && !f.reqd));
assert.ok(report.filters.some(f => f.fieldname === 'accounting_queue' && f.fieldtype === 'Check'));
assert.ok(report.filters.some(f => f.fieldname === 'origin_type'));
assert.ok(report.filters.some(f => f.fieldname === 'statement_scope' && f.options.includes('Contabilidad del core')));
assert.ok(report.filters.some(f => f.fieldname === 'source_account'));
assert.equal(report.filters.find(f => f.fieldname === 'difference_treatment').options, '\nA Favor Cliente\nA Favor Empresa');
assert.equal(report.bridge_reset().difference_treatment, '');
const col = {fieldname: 'balance_usd', fieldtype: 'Currency'}, base = v => `formatted:${v}`;
assert.match(report.formatter(null, null, col, {balance_usd: null}, base), /—/);
assert.equal(report.formatter(0, null, col, {balance_usd: 0}, base), 'formatted:0');
assert.equal(report.formatter(-7.35, null, col, {balance_usd: -7.35}, base), 'formatted:-7.35');
const amount = {fieldname: 'statement_amount_usd', fieldtype: 'Currency'};
assert.equal(report.formatter(-3.61, null, amount,
    {_is_statement_total:true, statement_amount_usd:-3.61, employer:'ACEREAL'}, base), '<strong>formatted:-3.61</strong>');
assert.match(report.formatter(null, null, amount, {_is_statement_total:true, statement_amount_usd:null}, base), /—/);
assert.equal(report.formatter(null, null, amount, {_is_statement_total:true}, base), '');
assert.equal(report.formatter(null, null, {fieldname:'statement_amount_nio',fieldtype:'Currency'},
    {statement_scope:'Gestión',statement_amount_nio:null}, base), '');
let drill, back;
const visible = {};
const filterValues = Object.fromEntries(report.filters.map(filter => [filter.fieldname, filter.default ?? '']));
Object.assign(filterValues, {from_date:'2025-04-01', cutoff_date:'2025-06-30', search:'Ana'});
const original = {...filterValues};
const live = {get_filter_value: key => filterValues[key], set_filter_value: values => Object.assign(filterValues, values),
    get_filter: name => ({get_value: () => filterValues[name], $wrapper:{toggle(value){visible[name]=value;}}}),
    refresh(){},
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
const differenceLink = report.formatter(-9.32, null, {fieldname:'pending_core_difference_usd', fieldtype:'Currency'},
    {employer:'Alfa', pending_core_difference_usd:-9.32}, base);
assert.match(differenceLink, /accounting_queue/);
assert.doesNotMatch(differenceLink, /component&quot;:&quot;pending_core_difference_usd/);
drill.call({'data-detail':JSON.stringify({employer:'Alfa', component:'', accounting_queue:1})}, {preventDefault(){}});
assert.equal(filterValues.accounting_queue, 1);
assert.equal(filterValues.component, '');
back();
assert.deepEqual(filterValues, scoped, 'The accounting queue drill restores the original summary filters');
const coreLink = report.formatter(635.38, null, {fieldname:'core_balance_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',core_balance_usd:635.38}, base);
assert.match(coreLink, /Contabilidad del core/);
drill.call({'data-detail':JSON.stringify({employer:'ACEREAL',component:'core_balance_usd',statement_scope:'Contabilidad del core'})}, {preventDefault(){}});
assert.equal(filterValues.statement_scope, 'Contabilidad del core');
assert.equal(filterValues.accounting_queue, 0);
back();
assert.deepEqual(filterValues, scoped);
Object.assign(filterValues, {statement_scope:'Gestión', component:'core_balance_usd'});
report.filters.find(filter => filter.fieldname === 'statement_scope').on_change(live);
assert.equal(filterValues.component, '', 'Changing sections clears an incompatible drill-down component');
Object.assign(filterValues, scoped);
const bridgeLink = report.formatter(-100.09, null, {fieldname:'reconciliation_difference_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',reconciliation_difference_usd:-100.09}, base);
assert.match(bridgeLink, /Conciliación/);
drill.call({'data-detail':JSON.stringify({employer:'ACEREAL',view_mode:'Conciliación'})}, {preventDefault(){}});
assert.equal(filterValues.view_mode, 'Conciliación');
assert.equal(filterValues.employer, 'ACEREAL');
assert.equal(filterValues.cutoff_date, scoped.cutoff_date, 'The explanation uses the same cut');
assert.equal(filterValues.from_date, '', 'Partial populations cannot be compared against full management');
assert.equal(filterValues.component, '');
assert.equal(filterValues.search, '');
assert.equal(filterValues.statement_scope, 'Todos');
live.cn_display_filters();
assert.equal(visible.cutoff_date, true);
assert.equal(visible.source_account, false);
assert.equal(visible.accounting_queue, false);
back();
assert.deepEqual(filterValues, scoped);
assert.equal(report.formatter(12, null, amount, {bridge_row_type:'difference',statement_amount_usd:12}, base), 'formatted:12');
assert.equal(report.formatter(null, null, {fieldname:'core_amount_usd',fieldtype:'Currency'},
    {bridge_row_type:'difference',statement_amount_usd:12}, base), '');
Object.assign(filterValues, {view_mode:'Conciliación', source_account:'160209', component:'client_credit_usd'});
report.filters[0].on_change(live);
assert.equal(filterValues.source_account, '');
assert.equal(filterValues.component, '');
assert.equal(filterValues.cutoff_date, scoped.cutoff_date);
const pendingLink = report.formatter(90, null, {fieldname:'management_pending_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',management_pending_usd:90}, base);
assert.match(pendingLink, /management_pending_usd/);
const signedCredit = report.formatter(-15, null, {fieldname:'client_credit_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',client_credit_usd:-15}, base);
assert.match(signedCredit, /formatted:-15/);
assert.match(signedCredit, /client_credit_usd/);
const netLink = report.formatter(75, null, {fieldname:'management_net_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',management_net_usd:75}, base);
assert.match(netLink, /Todos/, 'The net balance drill retains the comparison with accounting');
assert.doesNotMatch(netLink, /component":"management_net_usd/);
const adjustmentsLink = report.formatter(88.95, null, {fieldname:'deposit_documentation_adjustment_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',deposit_documentation_adjustment_usd:88.95}, base);
assert.match(adjustmentsLink, /deposit_documentation_adjustment_usd/);
assert.match(adjustmentsLink, /Todos/);
const calculatedLink = report.formatter(686.32, null, {fieldname:'calculated_balance_usd',fieldtype:'Currency'},
    {employer:'ACEREAL',calculated_balance_usd:686.32}, base);
assert.match(calculatedLink, /calculated_balance_usd/);
assert.match(calculatedLink, /Todos/);
assert.equal(report.formatter(75, null, amount,
    {_is_statement_total:true,statement_amount_usd:75,_total_component:'management_net_usd'}, base), '<strong>formatted:75</strong>');
console.log('OK: company statement summary/detail, monetary formatting and original filter restoration.');
