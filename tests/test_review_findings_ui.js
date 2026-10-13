const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js', 'utf8');
const context = vm.createContext({__: text => text, frappe: {pages: {'control-credinomina': {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.helpers = {renderPeriodCard, renderMonthSummary, summarizeMonth}; })();'), context);
const period = {name:'P', reconciliation_mode:'Operativa', collection_cycle:'Mensual',
    control_state:'conciliado', applied_usd:100, remitted_usd:100, quality_missing_basis_count:2};
const card = context.helpers.renderPeriodCard(period);
assert.ok(card.includes('Cobranza sin equivalente US$: 2'));
assert.ok(card.includes('Revisar base de comparación'));
const summary = context.helpers.summarizeMonth([period, {...period, name:'P2', quality_missing_basis_count:1}]);
assert.equal(summary.quality_missing_basis_count, 3);
assert.equal(summary.state, 'conciliado', 'The quality warning must not change financial coverage');
assert.ok(context.helpers.renderMonthSummary([period], 'E', '2026-09').includes('Revisar base de comparación'));
assert.ok(!context.helpers.renderPeriodCard({...period, quality_missing_basis_count:0}).includes('Revisar base de comparación'));
console.log('Review findings UI: conversion warnings remain separate from financial status');
