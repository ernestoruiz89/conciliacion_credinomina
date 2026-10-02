const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "../credinomina_reconciliation");
const dir = path.join(root, "conciliacion_credinomina/doctype/cn_accounting_import");
const shared = fs.readFileSync(path.join(root, "public/js/accounting_bulk.js"), "utf8");
const hooks = fs.readFileSync(path.join(root, "hooks.py"), "utf8");
for (const hook of ["doctype_js", "doctype_list_js"]) {
    assert.ok(hooks.includes(`${hook} = {"CN Accounting Import": "public/js/accounting_bulk.js"}`));
}

for (const view of ["list", "form"]) {
    for (const withScript of [false, true]) {
        const actions = {};
        let handlers, messages = 0, shown = 0, refreshed = 0;
        const ctx = vm.createContext({
            __: text => text,
            localStorage: {getItem: () => null},
            frappe: {
                listview_settings: {}, session: {user: "test"},
                model: {can_create: () => true},
                msgprint() { messages++; },
                require() { throw new Error("Must not request a public asset"); },
                ui: {
                    form: {on(_doctype, value) { handlers = value; }},
                    Dialog: function () {
                        this.fields_dict = {result: {$wrapper: {on() {}}}};
                        this.set_secondary_action = () => {};
                        this.set_secondary_action_label = () => {};
                        this.show = () => { shown++; };
                    },
                },
            },
        });
        // Frappe appends hook code to the standard form/list script.
        vm.runInContext(fs.readFileSync(path.join(dir, view === "list" ? "cn_accounting_import_list.js" : "cn_accounting_import.js"), "utf8"), ctx);
        if (withScript) {
            vm.runInContext(shared, ctx);
            // Loading both list and form metadata in one session is safe.
            vm.runInContext(shared, ctx);
        }
        const add = (label, fn) => { actions[label] = fn; };
        if (view === "list") {
            ctx.frappe.listview_settings["CN Accounting Import"].onload({
                page: {add_inner_button: add}, list_view_settings: {}, meta: {fields: []},
                setup_columns() {}, render_header() {}, refresh() { refreshed++; },
            });
        } else {
            handlers.refresh({doc: {currency: "USD", rows: []}, is_new: () => true,
                toggle_reqd() {}, toggle_display() {}, set_query() {}, set_df_property() {},
                set_intro() {}, add_custom_button: add});
        }
        if (view === "list") {
            assert.equal(typeof actions["Carga masiva"], "function");
            actions["Carga masiva"]();
            assert.equal(shown, withScript ? 1 : 0);
            assert.equal(messages, withScript ? 0 : 1);
        } else {
            assert.equal(actions["Carga masiva"], undefined);
            assert.equal(shown, 0);
            assert.equal(messages, 0);
        }
        if (withScript && view === "list") {
            ctx.frappe.credinomina.openAccountingBulk = complete => complete();
            actions["Carga masiva"]();
            assert.equal(refreshed, 1);
        }
    }
}
console.log("OK: bulk dialog embedded in form/list metadata, no assets dependency, safe missing-code feedback and refresh callback.");
