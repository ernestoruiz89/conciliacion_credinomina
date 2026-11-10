const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const dialogs = [], calls = [], messages = [];
let respond, reject, completed;
const control = () => ({disabled: false, label: "", hidden: false,
    prop(key, value) { this[key] = value; return this; },
    text(value) { this.label = value; return this; },
    toggle(value) { this.hidden = !value; }, hide() { this.hidden = true; }});
const context = vm.createContext({window: {}, __: text => text, format_currency: value => Number(value).toFixed(2), frappe: {
    utils: {escape_html: String}, msgprint: value => messages.push(value), set_route() {},
    call(options) { calls.push(options); return new Promise((resolve, fail) => {respond = resolve; reject = fail;}); },
    ui: {Dialog: function(options) {
        Object.assign(this, options);
        this.handlers = {}; this.showCount = 0; this.hidden = false;
        this.refreshButton = control(); this.fields_dict = {};
        for (const field of options.fields) this.fields_dict[field.fieldname] = {df: field, $input: control(), $wrapper: control()};
        this.fields_dict.related_html.$wrapper = {
            html: value => {this.html = value;}, find: () => this.refreshButton,
            on: (_event, selector, handler) => {assert.ok(!this.handlers[selector], "Bind each handler only once"); this.handlers[selector] = handler;},
        };
        this.show = () => {this.showCount++;};
        this.hide = () => {this.hidden = true; this.onhide();};
        this.set_title = value => {this.title = value;};
        dialogs.push(this);
    }},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/public/js/related_pending_modal.js", "utf8"), context);
const state = fields => ({employer: "E", client_number: "7", client_name: "Persona",
    applications: [{type: "Aplicación", document: "IMPORT", pending_usd: 20}],
    rows: [{type: "Depósito", document: "DEP", pending_usd: 10}], ...fields});
const show = data => {context.window.credinominaShowRelatedPending(data); return dialogs.at(-1);};
const refresh = dialog => dialog.handlers[".cn-refresh-related-pending"]();
const plain = value => JSON.parse(JSON.stringify(value));
const total = (dialog, kind) => new RegExp('class="cn-related-pending-summary-' + kind + '"[^>]*>([^<]*)').exec(dialog.html)[1];
(async () => {
    for (const [scope, method, args] of [
        [{context: {import_name: "I", source_row_name: "ROW"}}, "accounting_related_records.get_related_pending", {import_name: "I", source_row_name: "ROW"}],
        [{context: {remittance_name: "D", detail_row_name: "DETAIL"}}, "accounting_related_records.get_related_pending_for_remittance", {remittance_name: "D", detail_row_name: "DETAIL"}],
        [{client: "CLIENT"}, "reconciliation_workbench.get_data", {employer: "E", client: "CLIENT"}],
    ]) {
        const input = state(scope), before = JSON.stringify(input), dialog = show(input), count = calls.length;
        assert.ok(dialog.html.includes('cn-refresh-related-pending'));
        const running = refresh(dialog);
        await refresh(dialog);
        assert.equal(calls.length, count + 1, "Repeated clicks must not create simultaneous requests");
        assert.equal(calls.at(-1).method, "credinomina_reconciliation." + method);
        assert.deepEqual(plain(calls.at(-1).args), args);
        assert.equal(dialog.refreshButton.disabled, true);
        assert.equal(dialog.refreshButton.label, "Actualizando…");
        respond({message: state({applications: [], rows: [{type: "Depósito", document: "DEP", pending_usd: 4}]})});
        await running;
        assert.equal(total(dialog, "applications"), "0.00");
        assert.equal(total(dialog, "sources"), "4.00");
        assert.match(dialog.html, /cn-related-pending-difference-value[^>]*>-4\.00/);
        assert.ok(!dialog.html.includes('/cn-accounting-import/IMPORT'), "Reconciled applications disappear from the table");
        assert.equal(dialog.showCount, 1, "Refresh must reuse the open modal");
        assert.equal(dialog.hidden, false);
        assert.equal(dialog.refreshButton.disabled, false);
        assert.equal(JSON.stringify(input), before, "Refresh must not mutate the original query result");
        const lastHTML = dialog.html, failure = refresh(dialog);
        reject(new Error("Connection lost")); await failure;
        assert.equal(dialog.html, lastHTML, "Failed requests preserve the last successful view");
        assert.match(messages.at(-1), /Se conserva la última consulta/);
        assert.equal(dialog.refreshButton.disabled, false);
        const empty = refresh(dialog);
        respond({message: state({applications: [], rows: []})}); await empty;
        assert.equal(total(dialog, "applications"), "0.00");
        assert.equal(total(dialog, "sources"), "0.00");
        assert.ok(dialog.html.includes("No se encontraron aplicaciones pendientes"));
        const finalHTML = dialog.html, closing = refresh(dialog);
        dialog.hide(); respond({message: state({})}); await closing;
        assert.equal(dialog.html, finalHTML, "A late reply must not redraw a closed modal");
        assert.equal(dialog.showCount, 1);
    }
    const complementary = show(state({context: {complementary_item: "ITEM", client_choice: "CHOSEN"}, client: "CHOSEN",
        client_completion: {available: true, client: "CHOSEN", modified: "old"},
        on_complete_client: latest => {completed = latest.client_completion; return true;}}));
    const update = refresh(complementary);
    assert.equal(calls.at(-1).method, "credinomina_reconciliation.complementary_related_client.get_related_pending");
    assert.deepEqual(plain(calls.at(-1).args), {item_name: "ITEM", selected_client: "CHOSEN"});
    assert.equal(complementary.fields_dict.complete_client_fields.$input.disabled, true);
    respond({message: state({client_completion: {available: true, client: "CHOSEN", modified: "new"}})});
    await update;
    await complementary.fields_dict.complete_client_fields.df.click();
    assert.equal(completed.modified, "new", "Optional client completion must use the refreshed document version");
    assert.equal(complementary.fields_dict.complete_client_fields.$wrapper.hidden, true);
    const withoutCompletion = refresh(complementary);
    respond({message: state({client_completion: {available: false}})}); await withoutCompletion;
    assert.equal(complementary.fields_dict.complete_client_fields.$wrapper.hidden, true);
    const invalid = refresh(complementary), previous = complementary.html;
    respond({message: {needs_client_selection: true}}); await invalid;
    assert.equal(complementary.html, previous, "An unresolved identity must not appear as zero pending balance");
    console.log("OK: refresh in place, all origins, current totals, retry, duplicate protection, close and current completion metadata.");
})().catch(error => {console.error(error); process.exitCode = 1;});
