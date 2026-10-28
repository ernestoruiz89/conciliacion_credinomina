const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
let yes, no, confirmations = 0, dirty = false, failure = false, reloads = 0;
const requests = [], messages = [];
const context = vm.createContext({__: x => x, frappe: {
    ui: {form: {on() {}}}, confirm(_text, accept, reject) {confirmations++; yes=accept; no=reject;},
    msgprint: text => messages.push(text), show_alert() {},
    async call(request) {requests.push(request); if (failure) throw Error("Periodo cerrado");},
}});
vm.runInContext(fs.readFileSync("credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.js", "utf8"), context);
const row={name:"R1",idx:1}, frm={doc:{name:"DEP",modified:"STAMP",docstatus:1,detail_rows:[row]},
    is_new:()=>false,is_dirty:()=>dirty,get_perm:()=>true,reload_doc:async()=>{reloads++;}};
(async()=>{
    assert.equal(context.canUnreconcileRemittanceDetail(frm,row),true);
    assert.equal(context.canUnreconcileRemittanceDetail(frm,{...row,row_kind:"Diferencia documentada"}),false);
    dirty=true; context.unreconcileRemittanceDetail(frm,"",row.name); assert.equal(confirmations,0); dirty=false;
    context.unreconcileRemittanceDetail(frm,"",row.name);context.unreconcileRemittanceDetail(frm,"",row.name);
    assert.equal(confirmations,1);no();assert.equal(frm._cnUnreconcilingRow,null);
    context.unreconcileRemittanceDetail(frm,"",row.name);await yes();
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0].args)),{remittance_name:"DEP",detail_row_name:"R1",modified:"STAMP"});
    assert.equal(reloads,1);assert.equal(frm._cnUnreconcilingRow,null);
    context.unreconcileRemittanceDetail(frm,"",row.name);frm.doc.modified="NEW";await yes();assert.equal(requests.length,1);
    failure=true;context.unreconcileRemittanceDetail(frm,"",row.name);await assert.rejects(yes(),/cerrado/);
    assert.equal(frm._cnUnreconcilingRow,null);
    frm.doc.docstatus=2;assert.equal(context.canUnreconcileRemittanceDetail(frm,row),false);
    console.log("OK: row action, confirmation, permissions, stale form and retry.");
})().catch(error=>{console.error(error);process.exitCode=1;});
