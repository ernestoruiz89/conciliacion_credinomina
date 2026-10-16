const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
let dialog, requests = [], reloaded = 0, dirty = false, release;
const context = vm.createContext({__: text => text, format_currency: value => `USD ${value}`, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) { Object.assign(this, options); dialog = this; }
        show() {} hide() {} get_primary_btn() { return {prop() {}}; }
    }},
    utils: {escape_html: value => value.replaceAll('<', '&lt;').replaceAll('>', '&gt;')},
    msgprint() {}, show_alert() {},
    call: async args => {
        requests.push(args);
        if (args.method.endsWith('deletion_options')) return {message: {rows: [{entry_id: 'OP', date: '2025-06-01', description: 'Partida', amount_usd: 18.46, count: 5}]}};
        if (args.method.endsWith('preview_deletion')) return {message: {token: 'SERVER-TOKEN', changes: [
            {name: '<script>bad</script>', removed_count: 5, credit_pending_usd: 18.46},
            {name: 'CORE', removed_count: 5, compensated_usd: 0}]}};
        await new Promise(resolve => { release = resolve; });
        return {message: {}};
    },
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js'), 'utf8'), context);
const frm = {doc: {name: 'CREDIT'}, is_dirty: () => dirty, reload_doc: async () => { reloaded++; }};
(async () => {
    dirty = true;
    await context.cn_delete_complementary_history(frm, 'management');
    assert.equal(requests.length, 0);
    dirty = false;
    for (const kind of ['compensation', 'management']) {
        await context.cn_delete_complementary_history(frm, kind);
        assert.equal(requests.at(-1).args.kind, kind);
        const selector = dialog;
        assert.ok(selector.fields[0].options.includes('cortes históricos'));
        await selector.primary_action({operation: selector.fields[1].options[0]});
        const review = dialog;
        assert.ok(review.fields[0].options.includes('&lt;script&gt;'));
        assert.ok(!review.fields[0].options.includes('<script>'));
        assert.ok(review.fields[0].options.includes('USD 18.46'));
        const pending = review.primary_action({reason: 'Duplicado'});
        await review.primary_action({reason: 'Doble clic'});
        const call = requests.at(-1);
        assert.equal(call.args.reason, 'Duplicado');
        assert.equal(call.args.token, 'SERVER-TOKEN');
        assert.equal(call.args.entry_id, 'OP');
        assert.equal(call.args.kind, kind);
        release();
        await pending;
    }
    assert.equal(reloaded, 2);
    assert.equal(requests.filter(row => row.method.endsWith('delete_history')).length, 2);
    console.log('OK: both histories, preview, escaped totals, exact token and duplicate-click guard.');
})().catch(error => { console.error(error); process.exitCode = 1; });
