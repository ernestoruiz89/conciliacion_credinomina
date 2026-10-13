const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const base = path.join(__dirname, '../credinomina_reconciliation');
const read = relative => fs.readFileSync(path.join(base, relative), 'utf8');
const calls = [];
const context = vm.createContext({__: x => x, frappe: {
    listview_settings: {}, pages: {'control-credinomina': {}},
    utils: {escape_html: x => String(x).replaceAll('<', '&lt;').replaceAll('>', '&gt;')},
    call: args => new Promise((resolve, reject) => calls.push({args, resolve, reject})),
}});
vm.runInContext(read('conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item_list.js'), context);
const settings = context.frappe.listview_settings['CN Complementary Item'];
vm.runInContext(read('conciliacion_credinomina/page/control_credinomina/control_credinomina.js').replace(/\}\)\(\);\s*$/,
    'globalThis.cards = {summarizeMonth, renderPeriodCard}; })();'), context);
const period = {name:'P', status:'Conciliado', control_state:'conciliado', applied_usd:0, remitted_usd:0,
    applied_total_usd:100, remitted_total_usd:100, pending_usd:0, open_exception_count:1};
assert.equal(context.cards.summarizeMonth([period]).applied_usd, 100);
assert.equal(context.cards.summarizeMonth([period]).remitted_usd, 100);
const card = context.cards.renderPeriodCard(period);
assert.ok(card.includes('Conciliado') && card.includes('Excepciones abiertas'));
assert.ok(card.includes('100.00'));
const partial = context.cards.renderPeriodCard({reconciliation_mode:'Operativa',
    applied_total_usd:100, remitted_total_usd:80, pending_usd:20, control_state:'parcial'});
assert.ok(partial.includes('Pendiente US$') && partial.includes('20.00'));
const schema = JSON.parse(read('conciliacion_credinomina/doctype/cn_remittance_target/cn_remittance_target.json'));
const visible = schema.fields.filter(f => f.in_list_view);
assert.equal(visible.reduce((sum, f) => sum + f.columns, 0), 10);
assert.ok(visible.some(f => f.fieldname === 'notes'));
assert.ok(visible.some(f => f.fieldname === 'result'));
assert.ok(!visible.some(f => ['row_key','historical_application'].includes(f.fieldname)));
vm.runInContext(read('conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js')
    .split('async function loadPayingCompanies')[0], context);
assert.equal(context.targetDisplayLabel({historical_application:'opaque-hash'}), 'Aplicación vinculada');
assert.equal(context.targetDisplayLabel({historical_application:'opaque-hash',detail_row:'D'},
    [{name:'D',client_name:'Ana',loan_number:'123-1'}]), 'Ana · Crédito 123-1 · Aplicación vinculada');
assert.equal(context.targetDisplayLabel({notes:'Destino conservado'}), 'Destino conservado');

const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    let rendered = 0;
    const list = {data:[{name:'C', docstatus:1}], page:{add_inner_button(){}}, render(){rendered++;}};
    settings.onload(list);
    list.settings.before_render();
    assert.equal(settings.get_indicator(list.data[0])[0], 'Consultando conciliación');
    calls.shift().resolve({message:{C:{state:'Conciliado',pending_usd:0,management_status:'Pendiente',management_pending_usd:15}}});
    await tick();
    assert.equal(settings.get_indicator(list.data[0])[1], 'green');
    assert.equal(rendered, 1);
    const html = settings.formatters.accounting_status('Importada del core', {}, list.data[0]);
    assert.ok(html.includes('>Importada del core</span>'));
    assert.ok(html.includes('Gestión: Pendiente') && html.includes('Por devolver/aplicar US$: 15.00'));
    assert.ok(html.includes('white-space:nowrap') && !html.includes('<br>'));
    list.settings.before_render();
    const obsolete = calls.shift();
    list.data = [{name:'OTHER'}];
    list.settings.before_render();
    obsolete.resolve({message:{C:{state:'Conciliado'}}});
    calls.shift().reject(new Error('offline'));
    await tick();
    assert.equal(settings.get_indicator(list.data[0])[0], 'Saldo no disponible');
    assert.equal(list.data[0]._cn_balance, undefined);
    console.log('OK: financial cards, readable targets, asynchronous list states and stale/error responses.');
})().catch(error => {console.error(error); process.exitCode=1;});
