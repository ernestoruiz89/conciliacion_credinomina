const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const listeners = new Map();
const messages = [], progress = [];
let calls = 0, request, fail = false, release;
const context = {
    __: x => x,
    $: () => ({text: value => progress.push(value)}),
    frappe: {
        ui: {form: {on() {}}},
        realtime: {on: (e, cb) => listeners.set(e, cb), off: e => listeners.delete(e)},
        utils: {escape_html: x => x.replaceAll("<", "&lt;")},
        msgprint: x => messages.push(x),
        call: async args => { calls++; request = args;
            await new Promise(resolve => {release = resolve;});
            if (fail) throw new Error("Error de prueba");
            return {message: {deposit: "DEP-1", detail_rows: 10, detail_matched: 9, detail_pending: 1, targets: 2, periods: ["P1", "P2"]}};
        },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js"), "utf8"), context);

(async () => {
    const run = vm.runInContext("reconcileRemittance", context);
    const frm = {doc: {name: "DEP-1", result: "Parcial"}, is_dirty: () => false, reload_doc: async () => {}};
    const first = run(frm);
    await run(frm);
    assert.equal(calls, 1);
    const listener = [...listeners.values()][0];
    listener({remittance_name: "OTHER", progress_id: request.args.progress_id, percent: 30, message: "Ignorar"});
    listener({remittance_name: "DEP-1", progress_id: "stale", percent: 30, message: "Ignorar"});
    assert.equal(progress.length, 0);
    listener({remittance_name: "DEP-1", progress_id: request.args.progress_id, percent: 30, message: "Aplicaciones"});
    assert.equal(progress[0], "Conciliando: 30% — Aplicaciones");
    release(); await first;
    assert.equal(listeners.size, 0);
    assert.equal(frm.reconciliation_running, false);
    assert.match(messages[0].message, /Parcial/);
    assert.match(messages[0].message, /DEP-1/);
    assert.match(messages[0].message, /P1, P2/);
    assert.match(request.freeze_message, /este depósito/);
    assert.ok(!messages[0].message.includes("Se recalculó la empresa"));
    fail = true;
    const next = run(frm); release();
    await assert.rejects(next, /Error de prueba/);
    assert.equal(listeners.size, 0);
    assert.equal(frm.reconciliation_running, false);
    assert.equal(messages.length, 1);
    console.log("OK: progress isolation, double-click guard, summary and cleanup on failure.");
})().catch(error => {console.error(error); process.exitCode = 1;});
