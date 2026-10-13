const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js'), 'utf8');
const context = vm.createContext({__: text => text, frappe: {pages: {'control-credinomina': {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.helpers = {renderPeriodCollectionTable, renderOperationalSummary, applyPeriodDifferenceFilter, styles}; })();'), context);
const row = {client_name: 'Cliente <script>', client_number: '7740', loan_number: '13997-1', installment_number: '1',
    expected_usd: 28.43, deducted_usd: 28.43, applied_usd: 0, remitted_usd: 0,
    pending_payment_usd: 28.43, pending_payment_deposits: [{name: 'DEP <1>', amount_usd: 28.43}],
    quality_difference_usd: -28.43, quality_status: 'Aplicación insuficiente',
    application_comment: '0', remittance_detail: '[]', collection_shortfall_usd: 0};
const period = {expected_usd: 113.81, deducted_usd: 113.81, applied_usd: 85.38, remitted_usd: 85.38,
    pending_application_usd: 28.43, rows: [row, {...row, client_name: 'Conforme', quality_difference_usd: 0,
        quality_status: 'Conforme según cobranza'}]};
const table = context.helpers.renderPeriodCollectionTable(period);
assert.equal((table.match(/<th scope="col">/g) || []).length, 7);
assert.ok(table.includes('data-period-difference-filter'));
assert.ok(table.includes('1 fila con diferencia'));
assert.ok(table.includes('data-quality-difference="1"') && table.includes('data-quality-difference="0"'));
assert.ok(table.includes('No hay diferencias de cobranza contra aplicación'));
const visibleRows = [{dataset: {qualityDifference: '1'}, hidden: false}, {dataset: {qualityDifference: '0'}, hidden: false}];
const region = {hidden: false, querySelectorAll: () => visibleRows};
const empty = {hidden: true};
const detail = {querySelector: selector => selector.includes('collection-region') ? region : empty};
assert.equal(context.helpers.applyPeriodDifferenceFilter(detail, true), 1);
assert.deepEqual(visibleRows.map(item => item.hidden), [false, true]);
assert.equal(region.hidden, false);
assert.equal(empty.hidden, true);
assert.equal(context.helpers.applyPeriodDifferenceFilter(detail, false), 2);
assert.deepEqual(visibleRows.map(item => item.hidden), [false, false]);
assert.ok(table.includes('Pago por aplicar'));
assert.ok(table.includes('cn-payment-pending'));
assert.ok(table.includes('Cliente &lt;script&gt;') && !table.includes('<script>'));
assert.ok(table.includes('DEP%20%3C1%3E'));
assert.ok(!table.includes('Antecedente'));
assert.ok(table.includes('Ver detalle') && table.includes('Depósitos vinculados'));
assert.ok(table.includes('tabindex="0"'));
const summary = context.helpers.renderOperationalSummary(period);
assert.equal((summary.match(/class="cn-kpi /g) || []).length, 4);
assert.ok(summary.includes('Pago recibido por aplicar'));
assert.ok(summary.includes('Asignado'));
assert.ok(summary.includes('Ver control de cobranza'));
assert.ok(summary.includes('no es saldo a favor'));
const restricted = context.helpers.renderOperationalSummary({...period, pending_application_usd: null, payment_evidence_restricted: true});
assert.ok(restricted.includes('Vista parcial') && restricted.includes('>—</div>'));
const css = context.helpers.styles();
assert.match(css, /\.cn-period-detail \.cn-detail-table \.cn-number[^}]*white-space: nowrap/);
assert.match(css, /\.cn-period-detail \.cn-period-collections[^}]*min-width: 1080px/);
assert.match(css, /--cn-surface: var\(--bg-color/);
assert.match(css, /\.cn-panel \{[^}]*background: var\(--cn-surface\)/);
assert.match(css, /\.cn-matrix tbody th\.cn-company \{[^}]*background: var\(--cn-surface\)/);
assert.match(css, /\.cn-conciliado, \.cn-historico_conciliado \{ background: var\(--cn-success-bg\)/);
assert.doesNotMatch(css, /\.cn-panel \{[^}]*background: white/);
console.log('OK: period modal separates cash stages, keeps seven columns, details, escaped links and nowrap amounts.');
