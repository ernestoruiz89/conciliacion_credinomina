const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
let dialog, payload, saved = 0, calls = 0, response;
const alerts = [];
const context = vm.createContext({__: x => x, frappe: {
    pages: {"control-credinomina": {}},
    model: {with_doctype: async () => {}},
    meta: {get_docfield: () => ({options: "Por determinar\nOtro"})},
    ui: {Dialog: class {
        constructor(options) {this.options = options; this.disabled = false; dialog = this;}
        show() {} hide() {this.hidden = true;}
        get_primary_btn() {return {prop: (key, value) => {this.disabled = value;}};}
    }},
    call: args => {payload = args; calls++; return new Promise(resolve => {response = resolve;});},
    show_alert: alert => alerts.push(alert),
}});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.helpers = {applicationExceptionAction, showApplicationException}; })();"), context);
const {applicationExceptionAction: action, showApplicationException: show} = context.helpers;
const period = {name: "PER", employer: "<Empresa>", status: "Parcial"};
const row = {name: 'R"1', client_name: "Ana", client_number: "12", loan_number: "100-1", amount: 100,
    historical_remitted_usd: 70, historical_balance_usd: 30};
assert.ok(!source.includes("ID para distribución"));
assert.ok(source.includes('__("Pendiente US$")'));
assert.match(action(period, row, true), /Registrar excepción/);
assert.match(action(period, row, true), /R&quot;1/);
for (const balance of [0, -10, undefined]) assert.equal(action(period, {...row, historical_balance_usd: balance}, true), "—");
assert.equal(action(period, row, false), "—");
assert.match(action({...period, status: "Cerrado"}, row, true), /Período cerrado/);
const existing = action(period, {...row, historical_balance_usd: 0, exception_name: 'EX"1', exception_status: "Resuelta"}, true);
assert.match(existing, /Ver excepción/);
assert.ok(!existing.includes("Registrar excepción"));
assert.match(existing, /EX&quot;1/);
(async () => {
    await show(period, row, async () => {saved++;});
    assert.ok(dialog.options.fields.find(f => f.fieldname === "context").options.includes("&lt;Empresa&gt;"));
    const pending = dialog.options.fields.find(f => f.fieldname === "pending_usd");
    assert.equal(pending.read_only, 1);
    assert.equal(pending.default, 30);
    const task = dialog.options.primary_action({cause_category: "Otro", description: "Pago parcial", pending_usd: 999});
    await dialog.options.primary_action({description: "Doble clic"});
    assert.equal(calls, 1);
    assert.equal(dialog.disabled, true);
    assert.equal(payload.args.expected_pending_usd, 30);
    assert.equal(payload.args.application_id, 'R"1');
    response({message: {name: "EXC", created: true}});
    await task;
    assert.equal(saved, 1);
    assert.equal(dialog.hidden, true);
    assert.equal(dialog.disabled, false);
    assert.match(alerts[0].message, /registrada/);
    console.log("OK: conditional exception actions, existing cases, closed periods, modal context, immutable amount and duplicate-click protection.");
})().catch(error => {console.error(error); process.exitCode = 1;});
