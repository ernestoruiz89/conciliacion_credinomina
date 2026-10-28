const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, previewed = 0, removed = 0, reloads = 0; const values = {};
const ctx = vm.createContext({__: x => x, format_currency: x => `USD ${x}`, frappe: {
    utils: {escape_html: x => x}, ui: {form: {on() {}}, Dialog: function(options) {
        Object.assign(this, options); dialog = this;
        this.fields_dict = {result: {$wrapper: {html() {}, empty() {}}}};
        this.get_values = () => values; this.get_value = key => values[key];
        this.get_primary_btn = () => ({prop() {}}); this.show = () => {}; this.hide = () => {};
        this.set_primary_action = (label, action) => {this.primary_action = action;};
    }}, show_alert() {}, msgprint() {}, async call(request) {
        if (request.method.endsWith('distribution_history')) return {message: {entries: [{operation_id: 'OP', kind: 'Saldo a favor contra depósito', parent_document: 'DEP', amount_usd: 25.22}]}};
        if (request.method.endsWith('preview')) {previewed++; return {message: {token: 'TOKEN', destination: 'DEP', amount_usd: 25.22, remaining_usd: 25.22}};}
        removed++; assert.equal(request.args.token, 'TOKEN'); assert.equal(request.args.operation_id, 'OP'); assert.equal(request.args.reason, 'Error verificado');
    }
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), ctx);
(async () => {
    await ctx.cn_remove_split_distribution({doc: {name: 'CORE'}, is_dirty: () => false, reload_doc: async () => reloads++});
    values.operation = dialog.fields[0].options[1]; values.reason = 'Error verificado';
    await dialog.primary_action(values); assert.equal(previewed, 1); assert.equal(removed, 0);
    await dialog.primary_action(values); assert.equal(removed, 1); assert.equal(reloads, 1);
    console.log('OK: removal requires a checked preview and an explicit second action.');
})().catch(error => {console.error(error); process.exitCode = 1;});
