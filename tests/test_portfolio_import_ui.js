const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_credit_portfolio_snapshot/cn_credit_portfolio_snapshot.js"), "utf8");
let handlers, rpc, messages = [], order = [];
const context = vm.createContext({__: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]),
    frappe: {ui: {form: {on: (_, events) => {handlers = events;}}},
        call: args => {order.push("import"); return rpc(args);}, msgprint: msg => messages.push(msg)}});
vm.runInContext(source, context);
function form() {
    return {doc: {name: "P1", source_file: "/private/files/new.xlsx"}, is_new: () => false,
        is_dirty: () => true, save: async () => {order.push("save");},
        reload_doc: async function () {order.push("reload"); Object.assign(this.doc, {
            row_count: 123, matched_client_count: 120, unmatched_client_count: 3, status: "Importado con alertas"});},
        add_custom_button(label, callback) {this.button = callback;}};
}
(async () => {
    let frm = form(); handlers.refresh(frm);
    rpc = async () => ({message: {row_count: 123, created_employer_count: 2, created_client_count: 5}});
    const pending = frm.button(); await frm.button(); await pending;
    assert.deepEqual(order, ["save", "import", "reload"]);
    assert.equal(messages[0].indicator, "orange");
    assert.ok(messages[0].message.includes("123 créditos") && messages[0].message.includes("120"));
    assert.ok(messages[0].message.includes("Empresas creadas: 2. Clientes creados: 5."));
    assert.equal(frm._cn_importing_portfolio, false);
    for (const status of [0, 500, 502, 504]) {
        messages = []; order = []; frm = form();
        rpc = async () => {throw {status, responseText: '<html><script>bad</script></html>'};};
        await context.importPortfolioSnapshot(frm);
        assert.deepEqual(order, ["save", "import"]);
        assert.equal(messages[0].indicator, "red");
        assert.ok(!messages[0].message.includes("<html>"));
        if (status) assert.ok(messages[0].message.includes(`HTTP ${status}`));
        assert.equal(frm._cn_importing_portfolio, false);
    }
    messages = []; order = []; frm = form();
    frm.save = async () => {throw new Error("save failed");};
    await context.importPortfolioSnapshot(frm);
    assert.equal(order.length, 0);
    assert.ok(messages[0].message.includes("no se inició"));
    messages = []; order = []; frm = form();
    rpc = async () => ({message: {unchanged: true}});
    await context.importPortfolioSnapshot(frm);
    assert.equal(messages[0].title, "Corte sin cambios");
    messages = []; frm = form(); frm.reload_doc = async () => {throw new Error("reload");};
    await context.importPortfolioSnapshot(frm);
    assert.ok(messages[0].message.includes("terminó la importación"));
    messages = []; order = []; frm = form(); frm.doc.source_file = "";
    await context.importPortfolioSnapshot(frm);
    assert.equal(order.length, 0);
    assert.ok(messages[0].includes("Adjunte"));
    console.log("OK: portfolio import saves file first, prevents duplicates, reports success/unchanged and HTML HTTP errors safely.");
})().catch(error => {console.error(error); process.exitCode = 1;});
