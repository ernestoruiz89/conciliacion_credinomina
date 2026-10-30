const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const base = 'credinomina_reconciliation/';
const calls = [], dialogs = [];
const node = () => ({html() {return this;}, empty() {return this;}, prop() {return this;},
    appendTo() {return this;}, on() {return this;}, find() {return node();}, val() {return '';}});
const controls = {};
const context = vm.createContext({window: {}, $: node, __: text => text, format_currency: String, frappe: {
    pages: {'mesa-conciliacion': {}}, utils: {escape_html: value => String(value).replaceAll('<', '&lt;')},
    datetime: {str_to_user: value => value},
    call: args => new Promise((resolve, reject) => calls.push({args, resolve, reject})),
    set_route(route) {
        assert.equal(route, 'mesa-conciliacion');
        // Frappe 16 serializes route options into the URL, then reads strings.
        this.route_options.cn_workbench = JSON.parse(JSON.stringify(this.route_options.cn_workbench));
        if (this.serializeRoute) this.route_options.cn_workbench = JSON.stringify(this.route_options.cn_workbench);
    },
    ui: {make_app_page: () => ({main: {}, set_primary_action() {}, add_field(df) {
        const control = {df, value: '', description: '', get_value() {return this.value;},
            set_input(value) {this.value = value;}, set_description(value) {this.description = value;},
            async set_value(value) {this.value = value; df.change?.();}};
        controls[df.fieldname] = control;
        return control;
    }}), Dialog: class {
        constructor(options) {this.options = options; this.fields_dict = {related_html: {$wrapper: node()}}; dialogs.push(this);}
        show() {} hide() {this.hidden = true;}
    }},
}});
vm.runInContext(fs.readFileSync(base + 'public/js/related_pending_modal.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(base + 'conciliacion_credinomina/page/mesa_conciliacion/mesa_conciliacion.js', 'utf8'), context);
const page = context.frappe.pages['mesa-conciliacion'], wrapper = {};
const tick = () => new Promise(resolve => setImmediate(resolve));
const origin = {import_name: 'CONTA-LALA', source_row_name: 'ROW'};
const remittance = {remittance_name: 'DEP-LALA', detail_row_name: 'DETAIL'};
const complementary = {complementary_item: 'CN-COMP-LALA'};
(async () => {
    // Click the real modal button, then open/reopen the real page handlers.
    for (const serialized of [false, true]) for (const scope of [origin, remittance, complementary, null]) {
        context.frappe.serializeRoute = serialized;
        context.window.credinominaShowRelatedPending({employer: 'LALA', client_name: 'Josué', client_number: '7593',
            ...(scope ? {context: scope} : {client: 'CLIENT-ID'})});
        const modal = dialogs.at(-1);
        modal.options.secondary_action();
        assert.ok(modal.hidden);
        if (!wrapper.cn_workbench) page.on_page_load(wrapper);
        const opened = page.on_page_show(wrapper);
        await tick();
        const request = calls.shift();
        assert.ok(request, 'opening must consult the scoped client automatically');
        assert.equal(controls.employer.get_value(), 'LALA');
        assert.equal(request.args.args.employer, 'LALA');
        if (scope) assert.deepEqual(JSON.parse(request.args.args.context), scope);
        else assert.equal(request.args.args.client, 'CLIENT-ID');
        request.resolve({message: {employer: 'LALA', client: 'CLIENT-ID', client_name: 'Josué',
            client_number: '7593', applications: [], rows: []}});
        await opened;
        assert.equal(controls.client.get_value(), 'CLIENT-ID');
        assert.ok(!context.frappe.route_options.cn_workbench);
        assert.equal(wrapper.cn_workbench.context ? JSON.stringify(wrapper.cn_workbench.context) : null, scope ? JSON.stringify(scope) : null);
    }
    // A second serialized context layer also arrives as an object at the API.
    context.frappe.route_options = {cn_workbench: JSON.stringify({employer: 'LALA', context: JSON.stringify(origin)})};
    const nested = page.on_page_show(wrapper);
    assert.deepEqual(JSON.parse(calls[0].args.args.context), origin);
    calls.shift().resolve({message: {employer: 'LALA', client: null, client_name: '<Unknown>', client_number: '001', applications: [], rows: []}});
    await nested;
    assert.equal(controls.client.get_value(), '', 'never invent a client ID from a source number');
    assert.ok(controls.client.description.includes('&lt;Unknown>'));
    assert.ok(wrapper.cn_workbench.context, 'unregistered clients keep the origin scope');

    // Old responses cannot replace a newly selected company/client.
    const old = wrapper.cn_workbench.load(), obsolete = calls.shift();
    await controls.employer.set_value('OTHER');
    obsolete.resolve({message: {client: 'STALE', client_name: 'Stale', applications: [], rows: []}});
    await old;
    assert.equal(controls.employer.get_value(), 'OTHER');
    assert.equal(controls.client.get_value(), '');
    assert.equal(wrapper.cn_workbench.data, null);
    assert.equal(wrapper.cn_workbench.context, null);

    for (const invalid of ['{broken', 'null', '[]', JSON.stringify({context: '{broken'})]) {
        context.frappe.route_options = {cn_workbench: invalid};
        await page.on_page_show(wrapper);
        assert.equal(calls.length, 0, 'invalid navigation must not query another client');
        assert.equal(controls.employer.get_value(), '');
        assert.equal(controls.client.get_value(), '');
    }
    console.log('OK: related-pending button, object/URL scopes, import/deposit/client origins, visible filters and stale responses.');
})().catch(error => {console.error(error); process.exitCode = 1;});
