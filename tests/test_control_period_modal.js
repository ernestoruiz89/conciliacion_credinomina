const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js'), 'utf8');
const context = vm.createContext({__: text => text, frappe: {pages: {'control-credinomina': {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.helpers = {renderPeriodCollectionTable, renderOperationalSummary, styles}; })();'), context);
const row = {client_name: 'Cliente <script>', client_number: '7740', loan_number: '13997-1', installment_number: '1',
    expected_usd: 28.43, deducted_usd: 28.43, applied_usd: 0, remitted_usd: 0,
    pending_payment_usd: 28.43, pending_payment_deposits: [{name: 'DEP <1>', amount_usd: 28.43}],
    application_comment: '0', remittance_detail: '[]', collection_shortfall_usd: 0};
const period = {expected_usd: 113.81, deducted_usd: 113.81, applied_usd: 85.38, remitted_usd: 85.38,
    pending_application_usd: 28.43, rows: [row]};
const table = context.helpers.renderPeriodCollectionTable(period);
assert.equal((table.match(/<th scope="col">/g) || []).length, 7);
assert.ok(table.includes('Pago pendiente de aplicar'));
assert.ok(table.includes('cn-payment-pending'));
assert.ok(table.includes('Cliente &lt;script&gt;') && !table.includes('<script>'));
assert.ok(table.includes('DEP%20%3C1%3E'));
assert.ok(!table.includes('Antecedente'));
assert.ok(table.includes('Ver detalle') && table.includes('Depósitos vinculados'));
assert.ok(table.includes('tabindex="0"'));
const summary = context.helpers.renderOperationalSummary(period);
assert.equal((summary.match(/class="cn-kpi /g) || []).length, 4);
assert.ok(summary.includes('Pago recibido por aplicar'));
assert.ok(summary.includes('Depósito asignado'));
assert.ok(summary.includes('Ver deducción y otros controles'));
assert.ok(summary.includes('no es saldo a favor'));
const restricted = context.helpers.renderOperationalSummary({...period, pending_application_usd: null, payment_evidence_restricted: true});
assert.ok(restricted.includes('Vista parcial') && restricted.includes('>—</div>'));
const css = context.helpers.styles();
assert.match(css, /\.cn-period-detail \.cn-detail-table \.cn-number[^}]*white-space: nowrap/);
assert.match(css, /\.cn-period-detail \.cn-period-collections[^}]*min-width: 1080px/);
console.log('OK: period modal separates cash stages, keeps seven columns, details, escaped links and nowrap amounts.');
