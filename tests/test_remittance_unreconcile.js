const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let dialog, confirm, request, reloads = 0;
const context = {__: x => x, frappe: {
    ui: {form: {on() {}}, Dialog: function(options) {
        dialog = options;
        this.show = this.hide = this.disable_primary_action = this.enable_primary_action = () => {};
    }},
    confirm: (message, yes, no) => { confirm = {yes, no}; },
    call: async args => { request = args; }, show_alert() {},
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js', 'utf8'), context);
(async () => {
    context.unreconcileRemittance({doc: {name: 'DEP', modified: 'VERSION'},
        is_dirty: () => false, reload_doc: async () => { reloads++; }});
    dialog.primary_action({reason: 'Corrección'});
    assert.equal(request, undefined);
    confirm.no();
    assert.equal(request, undefined);
    dialog.primary_action({reason: 'Corrección'});
    await confirm.yes();
    assert.ok(request.method.endsWith('.unreconcile_remittance'));
    assert.equal(request.args.reason, 'Corrección');
    assert.equal(request.args.modified, 'VERSION');
    assert.equal(reloads, 1);
    console.log('OK: cancellation makes no request; confirmation sends reason and reloads.');
})();
