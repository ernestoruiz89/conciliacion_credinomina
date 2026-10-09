const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const calls = [], messages = [], events = {}, order = [];
let dialog, preview;
function mockWrapper() {
    const handlers = {}, elements = {};
    return {handlers, elements,
        on(event, selector, callback) { handlers[selector] = callback; },
        find(selector) {
            return elements[selector] ||= {props: {},
                text(value) { this.value = value; return this; },
                prop(name, value) { this.props[name] = value; return this; },
                toggle(value) { this.visible = value; return this; }};
        },
    };
}
const context = vm.createContext({
    __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    frappe: {
        ui: {form: {on: (name, handlers) => {events[name] = handlers;}}, Dialog: class {
            constructor(options) {
                this.options = options; dialog = this;
                this.fields_dict = {selection_preview: {$wrapper: mockWrapper()}};
                this.button = {prop(name, value) { this[name] = value; }};
            }
            show() { this.shown = true; }
            hide() { this.shown = false; }
            get_primary_btn() { return this.button; }
        }},
        utils: {escape_html: value => value.replace(/</g, "&lt;").replace(/>/g, "&gt;")},
        msgprint: value => messages.push(value),
        call: async options => {
            order.push("call"); calls.push(options);
            return {message: preview};
        },
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);
const frm = {doc: {name: "D", detail_periods: [{period: "P"}]}, is_new: () => false,
    is_dirty: () => true, save: async () => order.push("save"), reload_doc: async () => order.push("reload")};
async function run(fromCollection) {
    calls.length = messages.length = order.length = 0;
    frm.doc.detail_periods = [{period: "P"}];
    const action = events["CN Remittance Allocation"][fromCollection ? "use_collection_detail" : "use_applications_detail"];
    preview = {periods: ["P", "P2"], applied_usd: 100, total_usd: 60, deposit_usd: 50,
        rows: [{claim_id: "H:A", client_name: "<Ana>", deducted_usd: 50},
            {claim_id: "H:B", client_name: "Bea", deducted_usd: 10}], fingerprint: "F", replaces_detail: true};
    preview.collection_usd = 100;
    await action(frm);
    assert.ok(calls[0].method.endsWith(fromCollection ? ".preview_collection_detail" : ".preview_application_detail"));
    assert.ok(dialog.options.title.includes(fromCollection ? "cobranza" : "aplicaciones"));
    assert.deepEqual(order, ["save", "call"]);
    assert.equal(calls.length, 1); // Preview only, no mutation before confirmation.
    assert.ok(dialog.shown);
    assert.ok(dialog.options.fields[0].options.includes("&lt;Ana&gt;"));
    assert.ok(dialog.options.fields[0].options.includes("no coincide"));
    assert.equal(dialog.options.fields[1].fieldname, "replace_detail");
    assert.equal(dialog.options.fields[1].reqd, 1);
    const wrapper = dialog.fields_dict.selection_preview.$wrapper;
    assert.equal(wrapper.elements["[data-selected-total]"].value, "60.00");
    assert.equal(wrapper.elements["[data-select-all]"].props.checked, true);
    // Clearing all disables the action, with a server-call guard as well.
    wrapper.handlers["[data-select-all]"]({target: {checked: false}});
    assert.equal(dialog.button.disabled, true);
    await dialog.options.primary_action({replace_detail: 1});
    assert.equal(calls.length, 1);
    wrapper.handlers["[data-select-all]"]({target: {checked: true}});
    assert.equal(dialog.button.disabled, false);
    wrapper.handlers["[data-application-index]"]({target: {dataset: {applicationIndex: "1"}, checked: false}});
    assert.equal(wrapper.elements["[data-selected-total]"].value, "50.00");
    assert.equal(wrapper.elements["[data-select-all]"].props.indeterminate, true);
    assert.equal(wrapper.elements["[data-selection-warning]"].visible, false);
    const editAmount = (index, value) => wrapper.handlers["[data-application-amount]"]({
        target: {dataset: {applicationAmount: String(index)}, value},
    });
    for (const value of ["", "0", "-1", "1.001", "NaN", "Infinity"]) {
        editAmount(0, value);
        assert.equal(dialog.button.disabled, true);
        assert.equal(wrapper.elements["[data-amount-error]"].visible, true);
        await dialog.options.primary_action({replace_detail: 1});
        assert.equal(calls.length, 1);
    }
    editAmount(0, "45.25");
    assert.equal(dialog.button.disabled, false);
    assert.equal(wrapper.elements["[data-selected-total]"].value, "45.25");
    assert.equal(wrapper.elements["[data-selection-warning]"].visible, true);
    editAmount(0, "55.15"); // Also allow a detail larger than the original pending amount.
    editAmount(1, ""); // Invalid unselected rows do not block generation.
    assert.equal(dialog.button.disabled, false);
    assert.equal(wrapper.elements["[data-selected-total]"].value, "55.15");
    assert.equal(preview.rows[0].deducted_usd, 50);
    await dialog.options.primary_action({replace_detail: 1});
    assert.ok(calls[1].method.endsWith(fromCollection ? ".use_collection_detail" : ".use_application_detail"));
    assert.equal(calls[1].args.fingerprint, "F");
    assert.equal(calls[1].args.replace_detail, 1);
    assert.deepEqual(JSON.parse(calls[1].args.selected_claim_ids), ["H:A"]);
    assert.deepEqual(JSON.parse(calls[1].args.selected_amounts), {"H:A": "55.15"});
    assert.equal(order.at(-1), "reload");
    assert.equal(dialog.shown, false);
    assert.ok(!calls.some(call => /reconcile|submit/.test(call.method)));
    preview = {rows: []};
    dialog = undefined;
    await action(frm);
    assert.equal(dialog, undefined);
    assert.ok(messages.at(-1).includes(fromCollection ? "No hay filas de cobranza" : "No hay aplicaciones pendientes"));
    frm.doc.detail_periods = [];
    const before = calls.length;
    await action(frm);
    assert.equal(calls.length, before);
    console.log("OK: preview, pending totals, explicit replacement, save-first and no automatic reconciliation.");
}
async function visibility() {
    const shown = {};
    frm.toggle_display = (field, value) => {shown[field] = value;};
    frm.get_perm = () => true;
    const wrapper = {css() {}};
    frm.fields_dict = {use_applications_detail: {$wrapper: wrapper}, use_collection_detail: {$wrapper: wrapper}};
    frm.doc.employer = "EMP";
    frm.doc.detail_periods = [{period: "P"}];
    preview = true;
    await context.toggleApplicationDetailAction(frm);
    assert.equal(shown.use_collection_detail, true);
    preview = false;
    await context.toggleApplicationDetailAction(frm);
    assert.equal(shown.use_collection_detail, false);
    frm.doc.docstatus = 2;
    const before = calls.length;
    await context.toggleApplicationDetailAction(frm);
    assert.equal(calls.length, before);
    assert.equal(shown.use_collection_detail, false);
    frm.doc.docstatus = 0;
    const originalCall = context.frappe.call;
    const pending = [];
    context.frappe.call = () => new Promise(resolve => pending.push(resolve));
    const oldSelection = context.toggleApplicationDetailAction(frm);
    frm.doc.detail_periods = [{period: "OTHER"}];
    const newSelection = context.toggleApplicationDetailAction(frm);
    pending[0]({message: true});
    await oldSelection;
    assert.equal(shown.use_collection_detail, false);
    pending[1]({message: false});
    await newSelection;
    assert.equal(shown.use_collection_detail, false);
    context.frappe.call = originalCall;
}
run(false).then(() => run(true)).then(visibility).catch(error => {console.error(error); process.exitCode = 1;});
