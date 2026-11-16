const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const handlers = {}, requests = [], shown = [], messages = [];
let dirty = false, isNew = false, disabled = false;
const context = vm.createContext({window: {credinominaShowRelatedPending: data => shown.push(data)}, __: text => text,
    frappe: {ui: {form: {on: (doctype, events) => {handlers[doctype] = events;}}},
        msgprint: message => messages.push(message),
        call: request => new Promise((resolve, reject) => requests.push({request, resolve, reject})),
    }});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js', 'utf8'), context);
const frm = {doc: {name: 'DEP', employer: 'EMP', source_client_name: 'José Pérez', modified: 'VERSION', detail_rows: []},
    is_dirty: () => dirty, is_new: () => isNew, fields_dict: {view_related_pending: {$input: {prop: (_key, value) => {disabled = value;}}}}};
const click = () => handlers['CN Remittance Allocation'].view_related_pending(frm);
(async () => {
    dirty = true;
    await click();
    assert.equal(requests.length, 0);
    dirty = false; isNew = true;
    await click();
    assert.equal(requests.length, 0);
    isNew = false;
    const pending = click();
    await click();
    assert.equal(requests.length, 1, 'Double click opens only one consultation');
    assert.equal(disabled, true);
    assert.equal(requests[0].request.args.remittance_name, 'DEP');
    assert.equal(requests[0].request.args.detail_row_name, undefined, 'Header never substitutes an arbitrary detail row');
    requests[0].resolve({message: {client_number: '123', context: {remittance_name: 'DEP'}}});
    await pending;
    assert.equal(shown.length, 1);
    assert.equal(disabled, false);
    const stale = click();
    frm.doc.source_client_name = 'Other';
    requests[1].resolve({message: {client_number: 'OLD'}});
    await stale;
    assert.equal(shown.length, 1, 'Late replies cannot open another client');
    const failure = click();
    requests[2].reject(new Error('Permission denied'));
    await assert.rejects(failure, /Permission denied/);
    assert.equal(disabled, false);
    assert.equal(frm._cnHeaderPendingLoading, false);
    assert.equal(shown.length, 1);
    frm.doc.source_client_name = '';
    await click();
    assert.equal(requests.length, 3, 'No request without client evidence');
    assert.ok(messages.at(-1).includes('nombre o crédito'));
    assert.equal(typeof handlers['CN Remittance Detail'].view_related_pending, 'function');
    console.log('OK: header pending action, saved form, duplicates, stale replies, error recovery and existing detail action.');
})().catch(error => {console.error(error); process.exitCode = 1;});
