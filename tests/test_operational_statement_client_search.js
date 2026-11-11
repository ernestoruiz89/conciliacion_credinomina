const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const buttons = {}, requests = [], messages = [], refreshes = [];
let dialog;
const context = vm.createContext({__: text => text, frappe: {
    query_reports: {}, msgprint: message => messages.push(message),
    db: {get_value(doctype, name, fields) {
        return new Promise((resolve, reject) => requests.push({doctype, name, fields, resolve, reject}));
    }},
    ui: {Dialog: class {
        constructor(options) {
            this.options = options;
            this.values = Object.fromEntries(options.fields.map(field => [field.fieldname, field.default || ""]));
            dialog = this;
        }
        get_value(name) { return this.values[name]; }
        set_value(name, value) { this.values[name] = value; }
        get_primary_btn() { return {prop: (_name, value) => { this.disabled = value; }}; }
        show() { this.visible = true; }
        hide() { this.visible = false; this.options.onhide(); }
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/report/estado_de_cuenta_operativo/estado_de_cuenta_operativo.js", "utf8"), context);
const settings = context.frappe.query_reports["Estado de Cuenta Operativo"];
const values = Object.fromEntries(settings.filters.map(field => [field.fieldname, field.default ?? ""]));
Object.assign(values, {employer: "EMP", client_number: "OLD", national_id: "OLD-ID", loan_number: "OLD-LOAN", from_month: "2026-09-01"});
const shown = {};
const report = {
    _no_refresh: false,
    get_filter_value: name => values[name],
    get_filter(name) {
        return {get_value: () => values[name], $wrapper: {toggle: show => {shown[name] = show;}},
            async set_value(value) {
                const changed = value !== values[name];
                values[name] = value;
                if (changed && !report._no_refresh) await report.refresh();
            }};
    },
    async refresh() { refreshes.push({...values}); },
    page: {add_inner_button(label, action) {buttons[label] = action; return {text() {}};}},
};
settings.onload(report);
const select = () => dialog.options.primary_action({...dialog.values});
const choose = name => {dialog.values.client = name;};

(async () => {
    buttons["Buscar cliente"]();
    assert.equal(dialog.visible, true);
    const client = dialog.options.fields.find(field => field.fieldname === "client");
    assert.equal(client.options, "CN Client");
    assert.equal(client.get_query().filters.employer, "EMP");
    assert.equal(dialog.values.employer, "EMP");
    choose("CLIENT-DOCUMENT");
    const applying = select();
    await select();
    assert.equal(requests.length, 1, "Double click must not apply a second selection");
    assert.equal(requests[0].doctype, "CN Client");
    assert.equal(requests[0].name, "CLIENT-DOCUMENT", "Use the selected document, never an ambiguous name");
    assert.equal(dialog.disabled, true);
    requests[0].resolve({message: {client_number: "0007", employer: "EMP"}});
    await applying;
    assert.equal(refreshes.length, 1, "Query only after every identity filter has changed");
    assert.equal(values.client_number, "0007", "Preserve the actual client number, including padding");
    assert.equal(values.national_id, "");
    assert.equal(values.loan_number, "");
    assert.equal(values.employer, "EMP");
    assert.equal(values.from_month, "2026-09-01");
    assert.equal(values.position_type, "Aplicaciones, depósitos y partidas");
    assert.equal(values.only_open, 1);
    assert.equal(values.detailed_columns, 0);
    assert.equal(report._no_refresh, false);
    assert.equal(shown.client_number, true);
    assert.equal(shown.national_id, false);
    assert.equal(dialog.visible, false);

    buttons["Buscar cliente"]();
    choose("CLIENT-DOCUMENT");
    const same = select();
    requests.at(-1).resolve({message: {client_number: "0007", employer: "EMP"}});
    await same;
    assert.equal(refreshes.length, 2, "Selecting the same client also refreshes current balances");

    const before = {...values};
    for (const action of ["close", "change_company", "denied"]) {
        buttons["Buscar cliente"]();
        choose("OTHER");
        const pending = select();
        if (action === "close") dialog.hide();
        if (action === "change_company") {
            dialog.values.employer = "";
            dialog.options.fields.find(field => field.fieldname === "employer").onchange();
            assert.equal(dialog.values.client, "");
            assert.equal(Object.keys(dialog.options.fields.find(field => field.fieldname === "client").get_query().filters).length, 0);
        }
        if (action === "denied") requests.at(-1).reject(new Error("Permission denied"));
        else requests.at(-1).resolve({message: {client_number: "8", employer: "EMP"}});
        await pending;
        assert.deepEqual(values, before, "Cancelled, changed or unreadable selections must not replace report filters");
        assert.equal(refreshes.length, 2);
        assert.equal(dialog.disabled, false);
    }
    assert.equal(messages.length, 1);
    console.log("OK: client search, company scope, atomic filters, refresh, permissions and stale responses.");
})().catch(error => {console.error(error); process.exitCode = 1;});
