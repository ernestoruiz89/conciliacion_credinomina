const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const base = 'credinomina_reconciliation/conciliacion_credinomina/';
let dialog, resolveBalance;
const context = vm.createContext({__: x => x, format_currency: x => String(x),
    $: node => ({attr: key => node[key]}), frappe: {
        pages: {'control-credinomina': {}}, query_reports: {},
        datetime: {str_to_user: x => x},
        utils: {escape_html: x => String(x).replace(/</g, '&lt;').replace(/>/g, '&gt;'),
            get_form_link: (type, name) => `/app/${encodeURIComponent(type)}/${encodeURIComponent(name)}`},
        call: () => new Promise(done => {resolveBalance = done;}),
        ui: {form: {on() {}}, Dialog: class {
            constructor(options) {this.options = options; this.events = {};
                this.values = Object.fromEntries(options.fields.map(f => [f.fieldname, f.default]));
                this.wrapper = {html: x => {this.html = x;}, on: (e, selector, fn) => {this.events[selector] = fn;}};
                this.fields_dict = {help: {$wrapper:this.wrapper}}; dialog = this;}
            get_value(name) {return this.values[name];} set_df_property() {}
            get_field() {return {$wrapper:this.wrapper};} show() {this.visible = true;} hide() {this.visible = false;}
        }},
    }});
vm.runInContext(fs.readFileSync(`${base}page/control_credinomina/control_credinomina.js`, 'utf8')
    .replace(/\}\)\(\);\s*$/, 'globalThis.showWorkCase = showWorkCase; })();'), context);
vm.runInContext(fs.readFileSync(`${base}doctype/cn_complementary_item/cn_complementary_item.js`, 'utf8'), context);
for (const report of ['estado_de_cuenta_operativo', 'estado_de_cuenta_por_empresa']) {
    vm.runInContext(fs.readFileSync(`${base}report/${report}/${report}.js`, 'utf8'), context);
}
const tick = () => new Promise(done => setImmediate(done));
(async () => {
    const cases = [0,1].map(i => ({target_doctype:'CN Complementary Item', target_name:'C'+i,
        summary:`Case ${i} <script>`, next_action:'Verificar evidencia', amount_usd:100,
        amount_kind:'Importe observado', employer_name:'Empresa', client_number:'01'}));
    const viewer = context.showWorkCase(cases,0);
    assert.match(viewer.html,/Case 0 &lt;script&gt;/);
    assert.match(viewer.html,/Importe observado/);
    assert.match(viewer.html,/target="_blank" rel="noopener noreferrer"/);
    assert.match(viewer.html,/data-case-step="-1" disabled/);
    viewer.events['[data-case-step]'].call({'data-case-step':'1'});
    assert.match(viewer.html,/Case 1/);
    assert.match(viewer.html,/data-case-step="1" disabled/);
    viewer.options.primary_action(); assert.equal(viewer.visible,false);
    const form={doc:{name:'C',docstatus:0,category:'Por clasificar',amount_usd:50,accounting_source_key:'core'}, custom_buttons:{}, is_dirty:()=>false};
    context.cn_classify_pending(form);
    const preview=dialog;
    resolveBalance({message:{original_usd:100,used_usd:60,pending_usd:40,company_receivable_usd:5,
        amount_issue:'Revisar <dato>'}});
    await tick();
    assert.match(preview.html,/Distribuido \/ compensado US\$/);
    assert.match(preview.html,/>60</);
    assert.match(preview.html,/>40</);
    assert.match(preview.html,/CxC pendiente de cobro/);
    assert.match(preview.html,/Revisar &lt;dato&gt;/);
    assert.equal(form.doc.amount_usd,50,'Preview never rewrites evidence');
    context.cn_classify_pending(form); const stale=dialog.html;
    form.doc.name='OTHER'; resolveBalance({message:{original_usd:999}}); await tick();
    assert.equal(dialog.html,stale);
    let options;
    context.frappe.query_report={data:[{_available_statuses:['Pendiente de cobro','Parcialmente cobrada']}],
        get_filter:()=>({set_data:x=>{options=x;}}),get_filter_value:()=> 'Revisar cobros'};
    context.frappe.query_reports['Estado de Cuenta por Empresa'].after_datatable_render();
    assert.deepEqual(Array.from(options),['Pendiente de cobro','Parcialmente cobrada','Revisar cobros']);
    assert.equal(context.frappe.query_reports['Estado de Cuenta Operativo'].filters.find(f=>f.fieldname==='position_type').default,'Aplicación');
    context.frappe.listview_settings = {};
    vm.runInContext(fs.readFileSync(`${base}doctype/cn_complementary_item/cn_complementary_item_list.js`, 'utf8'), context);
    const indicator = context.frappe.listview_settings['CN Complementary Item'].get_indicator;
    const states = [];
    const headerForm = {doc:{name:'HEADER',docstatus:1,amount_usd:20,reconciliation_status:'Conciliado'}, is_new:()=>false,
        fields_dict:{financial_overview:{$wrapper:{html() {}}}},
        toolbar:{set_indicator:()=>states.push(indicator(headerForm.doc)[0])}};
    const loading = context.cn_render_complementary_balance(headerForm);
    assert.equal(states.at(-1),'Conciliado');
    resolveBalance({message:{original_usd:20,used_usd:20,pending_usd:0,reconciliation_state:'Conciliado'}});
    await loading;
    assert.equal(states.at(-1),'Conciliado');
    assert.equal(headerForm.doc.amount_usd,20);
    headerForm.doc.docstatus=2;
    headerForm.doc.reconciliation_status='Cancelada';
    assert.equal(indicator(headerForm.doc)[0],'Cancelada');
    headerForm.doc.docstatus=1;
    headerForm.doc.reconciliation_status='Conciliado';
    context.frappe.call=()=>Promise.reject(new Error('offline'));
    await context.cn_render_complementary_balance(headerForm);
    assert.equal(states.at(-1),'Conciliado');
    assert.equal(headerForm.doc.__onload.cn_balance,undefined,'Never retain a stale successful balance after an error');
    console.log('OK: case navigation, classification financial preview, safe HTML, stale responses and actual status suggestions.');
})().catch(error=>{console.error(error);process.exitCode=1;});
