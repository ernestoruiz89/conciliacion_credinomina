const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,
    '../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js'), 'utf8');
const context = vm.createContext({__: value => value, frappe: {pages: {'control-credinomina': {}}}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.renderWorkClient = renderWorkClient; })();'), context);
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
assert.ok(source.includes('${renderWorkClient(action)}'));
const missing = context.renderWorkClient({...item, client_name: '', client_number: ''});
assert.ok(missing.includes('Nombre no informado'));
assert.ok(missing.includes('Nro. Cliente: No informado'));
assert.ok(source.includes('${renderWorkClient(item)}'));
console.log('OK: client credit follow-up identity, leading zeros, missing data and safe HTML.');
