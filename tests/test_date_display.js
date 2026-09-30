// Run with: node tests/test_date_display.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
const inputs = [];
const context = {
    __: value => value,
    frappe: { pages: { "control-credinomina": {} }, datetime: {
        str_to_user(value) { inputs.push(value); return "23/04/2025"; },
    } },
};
vm.createContext(context);
vm.runInContext(source.replace(/\}\)\(\);\s*$/,
    "globalThis.helpers = {displayDate, historicalLabel, historicalSortDate}; })();"), context);
const {displayDate, historicalLabel, historicalSortDate} = context.helpers;
assert.equal(displayDate("2025-04-23 23:59:00"), "23/04/2025");
assert.deepEqual(inputs, ["2025-04-23"]); // native Frappe formatter; no timezone shift
assert.equal(displayDate(null), "");
assert.equal(displayDate(""), "");
assert.equal(inputs.length, 1);
const period = {historical_scope: "Fecha exacta", historical_application_date: "2025-04-23"};
assert.equal(historicalLabel(period), "Histórico · 23/04/2025");
assert.equal(historicalSortDate(period), "2025-04-23");
assert.equal(period.historical_application_date, "2025-04-23");
assert.equal(historicalLabel({historical_scope: "Rango de fechas", historical_start_date: "2025-04-23",
    historical_end_date: "2025-04-23"}), "Histórico · 23/04/2025 – 23/04/2025");
assert.equal(historicalLabel({historical_scope: "Mensual"}), "Histórico · Mensual");
console.log("OK: native date display, blanks, historical labels and unchanged ISO sorting.");
