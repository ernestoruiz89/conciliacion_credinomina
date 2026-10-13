const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
let main, picker, html = '', handlers = {}, calls = [], query = '', kind = '', storage = new Map();
const allRows = Array.from({length: 203}, (_, index) => ({row: index + 1, kind: 'Aplicación',
    client_name: `Cliente ${index + 1}`, employer_text: '<Origen>', description: '<script>asiento</script>',
    total_usd: 1, event_date: '2025-04-15', loan_number: `${index + 1}-1`, voucher: 'V'}));
const summary = {paginated_review: true, file_hash: 'HASH', rows: 203, group_count:203, groups:[],
    issues:[], issues_count:0, duplicates:[], already_imported:[], excluded:[],
    sections: {identified:{rows:0, groups:[], group_count:0, complementary_count:0, deposit_count:0},
        unidentified:{rows:203, groups:[], group_count:203, application_total_usd:203, complementary_count:0, deposit_count:0}}};
const state = {status:'Vista previa', phase:'preview', options:{source_file:'test.csv', currency:'USD', portfolio_snapshot:'P'}, summary};
const wrapper = {html(value) {html = value;}, on(event, selector, callback) {handlers[selector] = callback;}, empty() {},
    find(selector) {return {prop() {}, val:() => selector.includes('kind') ? kind : query, trigger() {}};}};
const context = vm.createContext({__:text=>text, format_currency:value=>`USD ${value}`, setTimeout:()=>0, clearTimeout() {},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    frappe:{session:{user:'Tester'}, utils:{escape_html:esc}, datetime:{str_to_user:value=>value}, msgprint() {},
        call:async request=>{calls.push(request);
            if (request.method.endsWith('get_unidentified_page')) {
                const start = request.args.start || 0;
                const selected = request.args.search ? allRows.filter(row=>row.client_name === request.args.search) : allRows;
                return {message:{rows:selected.slice(start,start+50),count:selected.length,start,page_size:50}};
            }
            if (request.method.endsWith('get_bulk_import_status')) return {message:state};
            if (request.method.endsWith('preview_bulk_import')) state.options = {...request.args, employer_assignments:JSON.parse(request.args.employer_assignments)};
            return {message:{token:'TOKEN'}};
        }, ui:{Dialog:function(options){
            this.opts=options;this.fields_dict={result:{$wrapper:wrapper}};
            this.get_values=()=>state.options;this.set_values=async()=>{};this.set_df_property=()=>{};
            this.get_primary_btn=()=>({prop:(name,value)=>{this.disabled=value;}});
            this.set_primary_action=(label,action)=>{this.primary=action;this.label=label;};this.primary=options.primary_action;
            this.set_secondary_action=action=>{this.secondary=action;};this.set_secondary_action_label=()=>{};
            this.show=()=>{};this.hide=()=>{};
            if(options.title.includes('Carga masiva')) main=this; else picker=this;
        }}}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/public/js/accounting_bulk.js','utf8'),context);
(async()=>{
    context.frappe.credinomina.openAccountingBulk();
    await main.primary();
    assert.ok(html.includes('Cliente 50') && !html.includes('Cliente 51'));
    assert.ok(html.includes('1–50 / 203') && html.includes('Buscar casos no identificados'));
    await handlers['.cn-review-page'].call({getAttribute:()=> '150'});
    assert.ok(html.includes('Cliente 151') && html.includes('151–200 / 203'));
    handlers['.cn-change-employer'].call({getAttribute:()=> '151'});
    await picker.opts.primary_action({employer:'Empresa A'});
    assert.ok(main.disabled && html.includes('Empresa A'));
    await handlers['.cn-review-page'].call({getAttribute:()=> '0'});
    await handlers['.cn-review-page'].call({getAttribute:()=> '150'});
    assert.ok(html.includes('Empresa A'), 'Choices survive page changes');
    query='Cliente 203';
    await handlers['.cn-review-search']();
    assert.ok(html.includes('Cliente 203') && html.includes('1–1 / 1'));
    main.secondary(); await main.primary();
    assert.equal(JSON.parse(calls.findLast(call=>call.method.endsWith('preview_bulk_import')).args.employer_assignments)['151'],'Empresa A');
    assert.ok(!calls.some(call=>call.method.endsWith('confirm_bulk_import')), 'Pagination/choices never import automatically');
    assert.ok(!html.includes('<script>asiento</script>'));

    let message, exportOptions;
    const rctx=vm.createContext({__:text=>text,$:element=>({attr:name=>element[name]}),frappe:{query_reports:{},utils:{escape_html:esc},
        datetime:{month_start:()=>'',month_end:()=>''},router:{slug:value=>value.toLowerCase().replaceAll(' ','-')},msgprint:value=>{message=value;}}});
    vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/report/control_mensual_de_movimientos_contables/control_mensual_de_movimientos_contables.js','utf8'),rctx);
    const report=rctx.frappe.query_reports['Control Mensual de Movimientos Contables'];
    assert.equal(report.export_hidden_cols,true);
    assert.equal(report.filters.find(filter=>filter.fieldname==='audit_columns').default,0);
    const record={source_row:257,source_idx:3,source_row_id:'ROW',accounting_import:'CONTA-A-4-2025-001',description:'<script>full</script>'};
    report.show_audit(record);
    assert.ok(message.message.includes('257') && message.message.includes('ROW'));
    assert.ok(message.message.includes('/app/cn-accounting-import/CONTA-A-4-2025-001'));
    assert.ok(message.message.includes('&lt;script&gt;full&lt;/script&gt;') && !message.message.includes('<script>'));
    let auditClick;
    report.onload({data:[record],get_filter:()=>({get_value:()=>'', $wrapper:{toggle(){}}}),
        export_report(){},export_dialog:{set_value:(field,value)=>{exportOptions={field,value};}},
        page:{add_inner_button:(label,action)=>{if(label==='Exportar auditoría') auditClick=action;return {text(){}};},
            wrapper:{off(){},on(){}}}});
    auditClick(); assert.equal(exportOptions.field,'include_hidden_columns'); assert.equal(exportOptions.value,1);
    console.log('OK: 203 casos paginados, búsqueda, selección conservada, origen exacto y exportación completa.');
})().catch(error=>{console.error(error);process.exitCode=1;});
