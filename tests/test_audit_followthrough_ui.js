const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const base = 'credinomina_reconciliation/conciliacion_credinomina/';
const context = vm.createContext({__: x => x, frappe: {query_reports: {}, listview_settings: {}, utils: {escape_html: String}}});
for (const name of ['estado_de_cuenta_operativo', 'estado_de_cuenta_por_empresa']) {
    vm.runInContext(fs.readFileSync(`${base}report/${name}/${name}.js`, 'utf8'), context);
}
for (const name of ['Estado de Cuenta Operativo', 'Estado de Cuenta por Empresa']) {
    const settings = context.frappe.query_reports[name];
    const active = name.includes('Operativo') ? 'client_number' : 'cutoff_date';
    const controls = Object.fromEntries(settings.filters.map(f => [f.fieldname, {
        get_value: () => f.fieldname === active ? 'active-value' : '',
        $wrapper: {toggle(value) {this.visible = value;}},
    }]));
    const buttons = {};
    const wrapper = {off() {}, on() {}};
    settings.onload({get_filter: name => controls[name], get_filter_value: name => controls[name]?.get_value(), page: {wrapper,
        add_inner_button(label, callback) {buttons[label] = callback; return {text() {}};}}});
    const hidden = Object.keys(controls).filter(key => controls[key].$wrapper.visible === false);
    assert.ok(hidden.length > 0);
    assert.equal(controls[active].$wrapper.visible, true, 'Active filters must remain visible');
    buttons['Más filtros']();
    hidden.forEach(key => assert.equal(controls[key].$wrapper.visible, true));
    buttons['Más filtros']();
    hidden.forEach(key => assert.equal(controls[key].$wrapper.visible, false));
    assert.equal(controls[active].$wrapper.visible, true);
}
assert.ok(!context.frappe.query_reports['Estado de Cuenta Operativo'].filters
    .find(f => f.fieldname === 'operational_status').options.some(x => x.startsWith('Deducido')));
vm.runInContext(fs.readFileSync(`${base}doctype/cn_complementary_item/cn_complementary_item_list.js`, 'utf8'), context);
const html = context.frappe.listview_settings['CN Complementary Item'].formatters.accounting_status('Registrada', {},
    {_cn_balance: {pending_usd: 0, company_receivable_usd: 500}});
assert.ok(html.includes('title="Registrada · Sin distribuir/compensar US$: 0.00 · Por cobrar US$: 500.00"'));
assert.ok(!html.includes('<br>'));
assert.ok(html.includes('white-space:nowrap') && html.includes('var(--text-color)'));
const schema = JSON.parse(fs.readFileSync(`${base}doctype/cn_complementary_item/cn_complementary_item.json`, 'utf8'));
assert.deepEqual(schema.fields.filter(f => f.in_standard_filter).map(f => f.fieldname).sort(),
    ['accounting_status', 'applied_usd', 'category', 'employer', 'legacy_manual_review', 'pending_usd', 'posting_date', 'reconciliation_status']);
console.log('OK: focused filters, visible active criteria, explicit receivable and management labels.');
