const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
let dialog, selected = {}, notice = "", html = "";
const row = {name:"A", client_name:"Cliente", loan_number:"L-1", event_date:"2025-04-30",
    amount_usd:137.33, application_adjustment_usd:0, net_applied_usd:137.33, protected_usd:111.32, adjustable_usd:26.01};
const context = vm.createContext({__:x=>x, format_currency:x=>String(x), frappe:{
    ui:{form:{on(){}}, Dialog:class {
        constructor(options) {dialog=this; this.options=options; this.fields_dict={applications:{$wrapper:{
            empty(){}, html: value=>{html=value;}, find:()=>({val:()=>"0"})}}};}
        get_value(){return "I";} show(){} hide(){this.hidden=true;}
    }}, datetime:{str_to_user:x=>x}, utils:{escape_html:x=>x},
    call:async()=>({message:[row]}), msgprint:text=>{notice=text;},
}});
vm.runInContext(fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.js"), "utf8"), context);
(async()=>{
    const frm={doc:{name:"NC", employer:"E", amount_usd:100}, is_dirty:()=>false,
        save:async()=>{}, set_value:async values=>{selected=values;}};
    await context.cn_select_original_application(frm);
    await dialog.options.fields[0].onchange();
    assert.ok(html.includes("Depósitos / reservas US$") && html.includes("Disponible para ajuste US$"));
    await dialog.options.primary_action();
    assert.equal(selected.application_adjustment_usd, 26.01);
    assert.equal(selected.related_application, "A");
    row.adjustable_usd=0; selected={};
    await dialog.options.primary_action();
    assert.ok(notice.includes("no tiene saldo disponible"));
    assert.equal(Object.keys(selected).length, 0);
    console.log("OK: mixed settlement picker displays protected cash and limits adjustment to pending balance.");
})().catch(error=>{console.error(error);process.exitCode=1;});
