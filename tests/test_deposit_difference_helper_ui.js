const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
let dialog, finish, dirty = false, fail = false, dialogs = 0;
const requests = [], messages = [];
const rows = [{row_name: 'P', client_name: 'CLIENTE', amount_usd: 10, pending_usd: 2, difference_reason: '', difference_notes: ''},
    {row_name: 'C', client_name: 'SALDO A FAVOR', amount_usd: 1, pending_usd: 0, difference_reason: '', difference_notes: ''}];
function button() {return {attrs: {}, show() {this.visible = true;}, prop(key, value) {this[key] = value;}, attr(values) {Object.assign(this.attrs, values);}};}
const source = fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js', 'utf8');
const context = vm.createContext({__: value => value, frappe: {
    ui: {form: {on() {}}, Dialog: class {
        constructor(options) {
            this.options = options; this.fields_dict = {}; this.close = button(); this.minimize = button();
            for (const field of options.fields) if (field.fieldname) this.fields_dict[field.fieldname] = {df: field, grid: {get_data: () => field.data}};
            dialog = this; dialogs++;
        }
        get_close_btn() {return this.close;}
        get_minimize_btn() {return this.minimize;}
        show() {this.display = true;}
        hide() {this.display = false; this.options.onhide.call(this);}
        toggle_minimize() {this.is_minimized = !this.is_minimized; this.options.on_minimize_toggle.call(this, this.is_minimized);}
        disable_primary_action() {this.busy = true;}
        enable_primary_action() {this.busy = false;}
        set_alert(message) {this.alert = message;}
    }},
    msgprint: value => messages.push(value), show_alert: value => messages.push(value),
    call: async request => {
        requests.push(request);
        if (request.method.endsWith('.load')) return {message: {modified: 'v1', rows}};
        if (fail) throw Error('Conflicto de versión');
        return new Promise(resolve => {finish = resolve;});
    },
}});
vm.runInContext(source, context);
const frm = {doc: {name: 'DEP', modified: 'v1', detail_rows: rows.map(row => ({...row, name: row.row_name}))},
    fields_dict: {}, is_dirty: () => dirty, reload_doc: () => {throw Error('Must not reload/reconcile');}};

(async () => {
    assert.match(source, /add_custom_button\(__\("Documentar diferencias del detalle"\).*__\("Más opciones"\)/);
    dirty = true; await context.reviewRemittanceDifferences(frm); assert.equal(requests.length, 0); dirty = false;
    await context.reviewRemittanceDifferences(frm);
    assert.equal(dialog.options.static, true, 'No outside-click or Escape dismissal');
    assert.equal(dialog.options.minimizable, true);
    assert.equal(dialog.close.visible, true, 'Static dialog still exposes explicit X');
    const grid = dialog.fields_dict.rows;
    assert.equal(grid.df.cannot_add_rows, true); assert.equal(grid.df.cannot_delete_rows, true);
    assert.ok(!grid.df.fields.find(field => field.fieldname === 'difference_notes').reqd);
    grid.df.data[0].difference_reason = 'Cliente';
    grid.df.data[0].difference_notes = 'Primero';
    dialog.toggle_minimize();
    assert.equal(dialog.minimize.attrs['aria-label'], 'Restaurar diferencias del detalle');
    await context.reviewRemittanceDifferences(frm);
    assert.equal(dialogs, 1); assert.equal(dialog.is_minimized, false);
    assert.equal(grid.df.data[0].difference_notes, 'Primero');
    const pending = dialog.options.primary_action();
    await dialog.options.primary_action();
    assert.equal(requests.length, 2, 'Double click sends a single batch');
    assert.equal(requests[1].args.rows.length, 1, 'Unedited blank reasons do not block the other row');
    assert.equal(requests[1].args.rows[0].row_name, 'P');
    assert.equal(requests[1].args.rows[0].amount_usd, undefined, 'No financial data may be sent');
    grid.df.data[0].difference_notes = 'Segundo'; // An edit while the request is in flight.
    finish({message: {modified: 'v2', updated: 1, rows: [{...rows[0], difference_reason: 'Cliente', difference_notes: 'Primero'}, rows[1]]}});
    await pending;
    assert.equal(dialog.display, true, 'Saving must keep the helper open');
    assert.equal(grid.df.data[0].difference_notes, 'Segundo', 'An in-flight edit must survive the response');
    assert.equal(frm.doc.detail_rows[0].difference_notes, 'Primero');
    assert.equal(frm.doc.modified, 'v2');
    const edited = dialog.options.primary_action();
    assert.equal(requests.at(-1).args.modified, 'v2');
    assert.equal(requests.at(-1).args.rows[0].difference_notes, 'Segundo');
    finish({message: {modified: 'v3', updated: 1, rows: [{...rows[0], difference_reason: 'Cliente', difference_notes: 'Segundo'}, rows[1]]}});
    await edited;
    const count = requests.length;
    await dialog.options.primary_action();
    assert.equal(requests.length, count, 'No-op saves do not make a request');
    grid.df.data[0].difference_notes = 'Tercero'; fail = true;
    await assert.rejects(dialog.options.primary_action(), /Conflicto/);
    assert.equal(grid.df.data[0].difference_notes, 'Tercero');
    assert.equal(dialog.display, true); assert.equal(dialog.busy, false);
    assert.equal(dialog.close.disabled, false);
    dialog.options.secondary_action();
    assert.equal(dialog.display, false); assert.equal(frm._cn_difference_helper, null);
    assert.ok(requests.every(request => request.method.startsWith('credinomina_reconciliation.deposit_difference_helper.')));
    console.log('OK: batch classification, editable/minimizable/static helper, safe repeat saves, retained drafts and explicit close.');
})().catch(error => {console.error(error); process.exitCode = 1;});
