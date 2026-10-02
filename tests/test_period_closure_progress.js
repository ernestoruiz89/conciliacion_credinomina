const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const listeners = new Map(), messages = [], progress = [];
let calls = 0, request, fail = false, release;
const context = vm.createContext({__: value => value,
    $: () => ({text: value => progress.push(value)}),
    frappe: {
        ui: {form: {on() {}}},
        realtime: {on: (event, callback) => listeners.set(event, callback),
            off: (event, callback) => {
                if (listeners.has(event)) assert.equal(listeners.get(event), callback);
                listeners.delete(event);
            }},
        show_alert: value => messages.push(value),
        call: async options => {
            calls++; request = options;
            await new Promise(resolve => {release = resolve;});
            if (fail) throw new Error("Saldo pendiente");
            return {message: {period: "PER", status: "Cerrado"}};
        },
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js"), "utf8"), context);
(async () => {
    async function untilCalls(count) {
        for (let attempt = 0; attempt < 10 && calls < count; attempt++) {
            await new Promise(resolve => setImmediate(resolve));
        }
        assert.equal(calls, count);
    }
    let saves = 0, reloads = 0;
    const frm = {doc: {name: "OLD"}, is_dirty: () => true,
        save: async () => {saves++; frm.doc.name = "PER";}, reload_doc: async () => {reloads++;}};
    const first = context.requestClose(frm);
    await context.requestClose(frm);
    await untilCalls(1);
    assert.equal(calls, 1); assert.equal(saves, 1);
    assert.equal(request.args.period_name, "PER", "Use name returned by save, not old name");
    assert.equal(request.freeze, true);
    const listener = listeners.get("cn_period_closure_progress");
    listener({period_name: "OTHER", progress_id: request.args.progress_id, percent: 20, message: "Ignore"});
    listener({period_name: "PER", progress_id: "OLD-TOKEN", percent: 20, message: "Ignore"});
    assert.equal(progress.length, 0);
    listener({period_name: "PER", progress_id: request.args.progress_id, percent: 75, message: "Validando depósitos"});
    assert.equal(progress[0], "Cerrando período: 75% — Validando depósitos");
    release(); await first;
    assert.equal(reloads, 1); assert.equal(listeners.size, 0); assert.equal(frm.closure_running, false);
    assert.equal(messages[0].indicator, "green");
    fail = true; const next = context.requestClose(frm); await untilCalls(2); release();
    await assert.rejects(next, /Saldo pendiente/);
    assert.equal(reloads, 1); assert.equal(messages.length, 1, "Never announce closure after failure");
    assert.equal(listeners.size, 0); assert.equal(frm.closure_running, false);
    frm.save = async () => {throw new Error("Error al guardar");};
    await assert.rejects(context.requestClose(frm), /Error al guardar/);
    assert.equal(calls, 2); assert.equal(listeners.size, 0); assert.equal(frm.closure_running, false);
    console.log("OK: closure progress, scoped tokens, dirty-save renaming, double-click protection and failure cleanup.");
})().catch(error => {console.error(error); process.exitCode = 1;});
