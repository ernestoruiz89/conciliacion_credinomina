const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js'), 'utf8');
const context = vm.createContext({__: value => value, frappe: {pages: {'control-credinomina': {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.renderWorkClient = renderWorkClient; globalThis.renderWorkException = renderWorkException; })();'), context);
const item = {kind: 'credit_management', category: 'Saldo a favor del cliente',
    client_name: 'ANA <script> & PEREZ', client_number: '00123'};
const html = context.renderWorkClient(item);
assert.ok(html.includes('ANA &lt;script&gt; &amp; PEREZ'));
assert.ok(html.includes('Nro. Cliente: 00123'));
assert.ok(!html.includes('<script>'));
assert.equal(context.renderWorkClient({...item, category: 'Saldo a favor de la empresa'}), '');
assert.equal(context.renderWorkClient({...item, kind: 'accounting_registration'}), html);
assert.equal(context.renderWorkClient({kind: 'deposit_detail'}), '');
assert.equal(context.renderWorkClient({...item, category: undefined, kind: 'pending_application'}), html);
assert.ok(!source.includes('${renderWorkClient(action)}')); // One identity per case, not repeated in each task.
assert.ok(source.includes('acciones del caso'));
const missing = context.renderWorkClient({...item, client_name: '', client_number: ''});
assert.ok(missing.includes('Nombre no informado'));
assert.ok(missing.includes('Nro. Cliente: No informado'));
assert.ok(source.includes('${renderWorkClient(item)}'));
const exception = {kind: 'open_exception', client_name: 'JUAN <ALBERTO>', client_number: '5584',
    loan_number: '001589-1', cause_category: 'Liquidación pendiente de pago',
    description: 'Liquidación <aplicada> pero aún no pagada'};
const identity = context.renderWorkClient(exception);
assert.ok(identity.includes('JUAN &lt;ALBERTO&gt;'));
assert.ok(identity.includes('Nro. Cliente: 5584 · Crédito: 001589-1'));
assert.ok(!identity.includes('<ALBERTO>'));
const detail = context.renderWorkException(exception);
assert.ok(detail.includes('Causa:</strong> Liquidación pendiente de pago'));
assert.ok(detail.includes('Motivo:</strong> Liquidación &lt;aplicada&gt; pero aún no pagada'));
assert.equal(context.renderWorkClient({kind: 'open_exception'}), '');
assert.equal(context.renderWorkException({kind: 'open_exception', cause_category: 'Por determinar'}), '');
assert.ok(context.renderWorkClient({...exception, kind: 'overdue_exception'}).includes('Crédito: 001589-1'));
assert.ok(source.includes('${renderWorkException(item)}'));
assert.ok(source.includes('${renderWorkException(action)}'));
console.log('OK: client credit follow-up identity, leading zeros, missing data and safe HTML.');
