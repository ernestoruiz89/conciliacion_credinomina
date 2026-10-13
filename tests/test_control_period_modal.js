const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js'), 'utf8');
const context = vm.createContext({__: text => text, frappe: {pages: {'control-credinomina': {}},
    datetime: {str_to_user: value => value}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.helpers = {renderPeriodCollectionTable, renderPeriodApplicationTable, renderPeriodExceptions, renderOperationalSummary, applyPeriodDifferenceFilter, applyApplicationDifferenceFilter, hasApplicationDepositDifference, styles}; })();'), context);
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
const unlinked = {name: 'U', event_date: '2026-09-15', client_name: 'Cliente <script>', client_number: '7080', loan_number: '11995-1',
    parent: 'IMPORT-1', reference: 'REF-1', net_applied_usd: 408.08, historical_remitted_usd: 0,
    historical_balance_usd: 408.08, historical_detail: '[]', deposit_match_status: 'Sin deposito'};
const settled = {...unlinked, name: 'S', client_name: 'Con depósito', reference: 'REF-2',
    historical_remitted_usd: 408.08, historical_balance_usd: 0, deposit_match_status: 'Depósito conciliado'};
const flagged = {...settled, name: 'F', reference: 'REF-3', deposit_match_status: 'Diferencia de importe'};
const unlinkedPeriod = {...period, reconciliation_mode: 'Operativa', historical_rows: [unlinked, settled, flagged],
    unlinked_application_rows: [unlinked]};
const unlinkedSummary = context.helpers.renderOperationalSummary(unlinkedPeriod);
assert.ok(unlinkedSummary.includes('1 aplicación sin cobranza vinculada'));
assert.match(unlinkedSummary, /408[.,]08/);
const applications = context.helpers.renderPeriodApplicationTable(unlinkedPeriod, false);
assert.ok(applications.includes('Mostrar solo diferencias entre aplicación y depósito'));
assert.ok(applications.includes('2 aplicaciones con diferencia'));
assert.equal((applications.match(/data-application-difference="1"/g) || []).length, 2);
assert.equal((applications.match(/data-application-difference="0"/g) || []).length, 1);
assert.ok(applications.includes('Cliente &lt;script&gt;') && !applications.includes('<script>'));
assert.ok(applications.includes('data-application-import="IMPORT-1"'));
assert.ok(applications.includes('Sin cobranza vinculada') && applications.includes('Cobranza vinculada'));
const historicApplications = context.helpers.renderPeriodApplicationTable({...unlinkedPeriod,
    reconciliation_mode: 'Historica', unlinked_application_rows: []}, true);
assert.ok(historicApplications.includes('<th>Excepción</th>'));
assert.ok(historicApplications.includes('Registrar excepción'));
assert.equal(context.helpers.hasApplicationDepositDifference(settled), false);
assert.equal(context.helpers.hasApplicationDepositDifference(flagged), true);
const applicationRows = [{dataset: {applicationDifference: '1'}, hidden: false},
    {dataset: {applicationDifference: '0'}, hidden: false}];
const applicationRegion = {hidden: false, querySelectorAll: () => applicationRows};
const applicationEmpty = {hidden: true};
const applicationDetail = {querySelector: selector => selector.includes('application-region') ? applicationRegion : applicationEmpty};
assert.equal(context.helpers.applyApplicationDifferenceFilter(applicationDetail, true), 1);
assert.deepEqual(applicationRows.map(item => item.hidden), [false, true]);
assert.equal(context.helpers.applyApplicationDifferenceFilter(applicationDetail, false), 2);
assert.deepEqual(applicationRows.map(item => item.hidden), [false, false]);
applicationRows[0].dataset.applicationDifference = '0';
assert.equal(context.helpers.applyApplicationDifferenceFilter(applicationDetail, true), 0);
assert.equal(applicationRegion.hidden, true);
assert.equal(applicationEmpty.hidden, false);
assert.equal(context.helpers.renderPeriodApplicationTable({...unlinkedPeriod, historical_rows: []}, false).includes('No hay aplicaciones asignadas'), true);
const exceptions = context.helpers.renderPeriodExceptions([{name: 'CN-EXC-11-2025-001',
    client_name: 'JUAN <ALBERTO> MONTOYA', client_number: '5584', loan_number: '001589-1',
    cause_category: 'Por determinar', description: 'Liquidación aplicada pero aún no pagada',
    amount_usd: 840.56, status: 'Abierta', next_action: 'Revisar pago',
    assigned_to: 'Usuario oculto', evidence_file: 'archivo-oculto.pdf'}]);
assert.equal((exceptions.match(/<th>/g) || []).length, 7);
assert.ok(exceptions.includes('JUAN &lt;ALBERTO&gt; MONTOYA'));
assert.ok(exceptions.includes('Nro. Cliente: 5584') && exceptions.includes('Crédito: 001589-1'));
assert.ok(exceptions.includes('Revisar pago') && exceptions.includes('Liquidación aplicada pero aún no pagada'));
assert.ok(!exceptions.includes('Responsable') && !exceptions.includes('Soporte'));
assert.ok(!exceptions.includes('Usuario oculto') && !exceptions.includes('archivo-oculto.pdf'));
assert.ok(context.helpers.renderPeriodExceptions([]).includes('No hay excepciones abiertas'));
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
console.log('OK: period modal filters collection and application/deposit differences in both period modes.');
