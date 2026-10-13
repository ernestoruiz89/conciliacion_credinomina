const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_period/cn_reconciliation_period.js', 'utf8');
let events;
const context = vm.createContext({__:x=>x, frappe:{ui:{form:{on:(_,handlers)=>{events=handlers;}}},
    session:{user:'Administrator'},user:{has_role:()=>true}}});
vm.runInContext(source,context);
const frm={doc:{name:'P',employer:'E',status:'Pendiente',reconciliation_mode:'Operativa',collection_rows:[{}]},
    fields:[],buttons:new Set(),get_perm:()=>true,is_new:()=>false,set_df_property(){},add_custom_button(label){this.buttons.add(label);}};
events.refresh(frm);
for(const label of ['Reconocer cobranza como detalle de la empresa','Reconocer cobranza por depósito','2. Cargar deducción de empresa','Plantilla de detalle empresa'])
    assert.ok(!frm.buttons.has(label));
assert.ok(frm.buttons.has('1. Cargar cobranza'));
assert.ok(frm.buttons.has('Conciliación 1: validar aplicaciones'));
console.log('OK: collection remains available; obsolete employer-detail actions are absent.');
