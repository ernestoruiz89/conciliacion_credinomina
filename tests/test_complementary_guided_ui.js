const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
let dialog, messages = [], routes = [];
const handlers = {};
const context = vm.createContext({__: x => x, format_currency: x => String(x), frappe: {
    utils: {escape_html: x => String(x).replace(/</g, '&lt;').replace(/>/g, '&gt;')},
    msgprint: x => messages.push(x), set_route: (...args) => routes.push(args),
    ui: {form: {on: (type, value) => {handlers[type] = value;}}, Dialog: class {
        constructor(options) {this.options = options; this.values = Object.fromEntries(options.fields.map(f => [f.fieldname, f.default]));
            this.fields_dict = {help: {$wrapper: {html: x => {this.help = x;}}}}; dialog = this;}
        get_value(name) {return this.values[name];} set_df_property() {} show() {this.visible = true;} hide() {this.visible = false;}
    }},
}});
vm.runInContext(fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js', 'utf8'), context);
const form = (doc) => ({doc, custom_buttons: {}, saved: 0, get_perm: () => true,
    async set_value(values, value) {Object.assign(this.doc, typeof values === 'string' ? {[values]: value} : values);},
    async save() {this.saved++;}, scroll_to_field(value) {this.scrolled = value;}});
const choices = () => dialog.options.fields.find(f => f.fieldname === 'action').options;
(async () => {
    const draft = form({docstatus: 0, accounting_source_key: 'core', category: 'Por clasificar', amount_usd: 40});
    context.cn_classify_pending(draft);
    assert.ok(choices().includes('Preparar partida de depósito'));
    assert.ok(choices().includes('Marcar fuera del flujo de depósitos'));
    assert.ok(!choices().includes('Compensar con otra partida'), 'Reuse actual permitted actions, not invented availability');
    await dialog.options.primary_action({action: 'Marcar fuera del flujo de depósitos', notes: ' '});
    assert.equal(draft.saved, 0);
    assert.equal(dialog.visible, true);
    await dialog.options.primary_action({action: 'Marcar fuera del flujo de depósitos', notes: 'Revisar clasificación original'});
    assert.equal(draft.saved, 1);
    assert.equal(draft.doc.review_action, 'No conciliatoria');
    assert.equal(draft.doc.docstatus, 0);
    assert.equal(draft.doc.amount_usd, 40, 'Classification does not zero or settle anything');
    const credit = form({docstatus: 1, category: 'Saldo a favor del cliente', credit_pending_usd: 20, registered_deposit: 'D'});
    credit.custom_buttons['Abrir depósito'] = true;
    credit.custom_buttons['Compensar con otra partida'] = true;
    context.cn_classify_pending(credit);
    assert.equal(dialog.get_value('action'), 'Documentar devolución o aplicación');
    assert.ok(choices().includes('Documentar devolución o aplicación'));
    assert.ok(!choices().includes('Marcar fuera del flujo de depósitos'));
    await dialog.options.primary_action({action: 'Abrir depósito'});
    assert.equal(routes.at(-1)[2], 'D');
    let html = '', resolve;
    const preview = form({registered_deposit: 'D', credit_detail_row: 'R'});
    preview.fields_dict = {credit_detail_preview: {$wrapper: {html: value => {html = value;}}}};
    context.frappe.call = () => new Promise(done => {resolve = done;});
    const request = context.cn_render_credit_detail(preview);
    preview.doc.credit_detail_row = '';
    await context.cn_render_credit_detail(preview);
    resolve({message: {rows: [{name: 'R', client_name: 'Old client', amount_usd: 10}]}});
    await request;
    assert.doesNotMatch(html, /Old client/);
    assert.match(html, /Excedente fuera/);
    const toolbar = [];
    const managed = form({name:'C', docstatus:1, category:'Saldo a favor del cliente',
        registered_deposit:'D', result:'Saldo a favor documentado', credit_pending_usd:15,
        credit_management_status:'Parcialmente resuelto', credit_history:'[{"amount":5}]'});
    Object.assign(managed, {fields_dict:{}, is_new:()=>false, trigger(){}, set_df_property(){}, toggle_display(){},
        dashboard:{set_headline_alert(){}}, add_custom_button:(label, action, group)=>toolbar.push({label,action,group}),
        change_custom_button_type:(label, group, type)=>{managed.primary = {label,type};}});
    handlers['CN Complementary Item'].refresh(managed);
    assert.equal(managed.primary.label, 'Documentar devolución o aplicación');
    assert.equal(managed.primary.type, 'primary');
    assert.equal(toolbar.find(button=>button.label === 'Revertir gestión').group, 'Más acciones');
    managed.doc.credit_pending_usd = 0;
    delete managed.primary;
    toolbar.length = 0;
    handlers['CN Complementary Item'].refresh(managed);
    assert.ok(!toolbar.some(button=>button.label === 'Documentar devolución o aplicación'), 'A resolved credit has no new management action');
    console.log('OK: guided classification, no artificial settlement, permissions, descriptive link and stale response.');
})().catch(error => {console.error(error); process.exitCode = 1;});
