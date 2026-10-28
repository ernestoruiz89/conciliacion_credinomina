const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const context = vm.createContext({__: text => text, frappe: {query_reports: {}}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/report/revision_de_partidas_manuales/revision_de_partidas_manuales.js", "utf8"), context);
const settings = context.frappe.query_reports["Revision de Partidas Manuales"];
assert.deepEqual(Array.from(settings.filters, field => field.fieldname),
    ["employer", "item", "from_date", "to_date", "include_tolerance"]);
assert.equal(settings.filters.find(field => field.fieldname === "include_tolerance").default, 1);
assert.equal(settings.open_details, undefined);
console.log("OK: manual review keeps inventory filters without link actions.");
