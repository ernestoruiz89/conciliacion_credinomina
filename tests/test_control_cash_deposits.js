const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,
    "../credinomina_reconciliation/conciliacion_credinomina/page/control_credinomina/control_credinomina.js"), "utf8");
const handlers = {};
let html, dialog, route;
const root = {appendTo() {return this;}, html(value) {html = value; return this;},
    on(event, selector, fn) {handlers[selector] = fn; return this;}};
const deposit = {name: "D1", employer: "E", month: "2025-05", date: "2025-05-20", reference: '<DEP&1>',
    total_usd: 1000, credits_usd: 800, other_usd: 100, credit_balance_usd: 100,
    unclassified_usd: 0, review_usd: 0, original_amount: 36624.3, currency: "NIO",
    result: "Parcial con saldo a favor", settled: false, needs_review: false, shared: true,
    destinations: [
        {type: "Créditos", label: "P-MARZO", month: "2025-03", amount_usd: 300},
        {type: "Créditos", label: "P-ABRIL", month: "2025-04", amount_usd: 500},
        {type: "Partida complementaria", label: "X1 · Cobranza administrativa", month: "2025-04", amount_usd: 100},
    ]};
const data = {year: 2025, periods: [{name: "P-ABRIL", employer: "E", month: "2025-04",
    reconciliation_mode: "Historica", control_state: "historico_conciliado", applied_usd: 500, remitted_usd: 500}],
    cash_deposits: [deposit, {...deposit, name: "D2", employer: "SOLO-DEPOSITOS", month: "2025-06", date: "2025-06-01"}],
    open_deposits: [{source_doctype: "CN Remittance Allocation", parent: "D1", event_date: "2025-05-20",
        employer_text: "E", reference: "REF-ABIERTA", voucher: "V1", amount: 1000, currency: "USD",
        allocated_usd: 800, unallocated_usd: 200, justified_surplus_usd: 100, unclassified_usd: 100,
        allocation_detail: JSON.stringify([{referencia: "DESGLOSE-NO-VISIBLE", importe_usd: 800}])}], totals: {}};
const context = vm.createContext({__: text => text, $: value => typeof value === "string" ? root : {attr: name => value[name]},
    frappe: {pages: {"control-credinomina": {}}, datetime: {str_to_user: value => value.split("-").reverse().join("/")},
        set_route: (...args) => {route = args;}, call: async () => ({message: data}), ui: {
            make_app_page: () => ({main: {}, add_field: df => ({df, refresh() {}, set_input() {},
                get_value: () => df.fieldname === "year" ? "2025" : ""}), add_button() {}, set_primary_action() {}}),
            Dialog: class {
                constructor(options) {dialog = this; this.options = options; this.events = {}; this.wrapper = {
                    html: value => {this.html = value;}, on: (event, selector, fn) => {this.events[selector] = fn;}};}
                get_field() {return {$wrapper: this.wrapper};} show() {this.shown = true;} hide() {this.shown = false;}
            },
        }},
});
vm.runInContext(source.replace(/\}\)\(\);\s*$/, "globalThis.helpers = {summarizeCash, renderCashDistribution, renderCashCard, renderMonthSummary}; })();"), context);
const {summarizeCash, renderCashDistribution, renderCashCard, renderMonthSummary} = context.helpers;
assert.equal(summarizeCash([deposit, deposit]).total_usd, 1000);
assert.equal(summarizeCash([deposit, deposit]).count, 1);
assert.equal(summarizeCash([deposit]).creditCount, 1);
assert.equal(summarizeCash([deposit]).settled, 0);
assert.equal(summarizeCash([{...deposit, needs_review: true}]).review, 1);
assert.equal(summarizeCash([{name: "A", total_usd: 0.1}, {name: "B", total_usd: 0.2}]).total_usd, 0.3);
const breakdown = renderCashDistribution(deposit);
assert.ok(breakdown.includes('class="cn-detail-table cn-cash-distribution-summary"'));
assert.ok(!breakdown.includes('class="cn-detail-table cn-cash-summary"'), "Summary table must not inherit the calendar's display:block styling");
assert.match(source, /\.cn-cash-distribution \{ font-size: 13px; line-height: 1\.5; \}/);
assert.match(source, /\.cn-cash-distribution \.cn-detail-table th,[\s\S]*?\.cn-cash-distribution caption \{ font-size: inherit; line-height: inherit; \}/);
assert.match(source, /\.cn-cash-distribution \.cn-kpi-value \{ font-size: 20px; line-height: 1\.3; \}/);
const personalDetail = renderCashDistribution({...deposit, destinations: [{
    type: "Créditos", label: "P-MARZO", month: "2025-03", amount_usd: 300,
    people: [{client_name: "Ana <Pérez>", client_number: "100", loan_number: "1000-1", amount_usd: 200},
             {client_name: "Luis", client_number: "200", loan_number: "2000-1", amount_usd: 100}],
}]});
assert.ok(personalDetail.includes("Distribución por persona"));
assert.ok(personalDetail.includes("Nro. Cliente") && personalDetail.includes("Asignado US$"));
assert.ok(personalDetail.includes("Ana &lt;Pérez&gt;") && !personalDetail.includes("Ana <Pérez>"));
assert.ok(personalDetail.includes("Luis") && personalDetail.includes("1000-1") && personalDetail.includes("2000-1"));
assert.ok(personalDetail.includes("200.00") && personalDetail.includes("100.00"));
assert.match(personalDetail, /Total detalle por cliente<\/th><th class="cn-number">[^<]*300\.00/);
assert.match(breakdown, /Total resumen<\/th><th class="cn-number">[^<]*1,000\.00/);
assert.ok(breakdown.includes('class="cn-kpis cn-cash-kpis"'));
assert.equal((breakdown.match(/class="cn-kpi-label"/g) || []).length, 4);
assert.match(breakdown, /cn-cash-result"><div class="cn-kpi-label">Estado de conciliación<\/div><div class="cn-kpi-value">Por revisar<\/div>/);
assert.ok(breakdown.includes("Resultado registrado: Parcial con saldo a favor"));
for (const result of ["Conciliado", "Revisar detalle", "Pendiente", "Saldo a favor documentado", "<Revisar>", ""]) {
    const markup = renderCashDistribution({...deposit, result});
    const resultCard = markup.slice(markup.indexOf('<div class="cn-kpi cn-cash-result">'), markup.indexOf('<div class="cn-list-scroll">'));
    assert.ok(resultCard.includes(result === "<Revisar>" ? "&lt;Revisar&gt;" : result || "Pendiente"));
    assert.ok(!resultCard.includes("<Revisar>"));
}
assert.ok(breakdown.includes("Referencia del depósito") && breakdown.includes("Depositado total"));
for (const currency of ["NIO", "USD"]) {
    const markup = renderCashDistribution({...deposit, currency});
    const bankKpi = markup.slice(markup.indexOf('Cuenta bancaria'), markup.indexOf('<div class="cn-kpi cn-kpi-remitted">'));
    assert.ok(bankKpi.includes(`Original: ${currency} 36,624.30`));
    const usdKpi = markup.slice(markup.indexOf('<div class="cn-kpi cn-kpi-remitted">'), markup.indexOf('<div class="cn-kpi cn-cash-result">'));
    assert.ok(usdKpi.includes('Depositado total USD'));
    assert.ok(!usdKpi.includes('Original:'));
    assert.equal((markup.match(/Original:/g) || []).length, 1);
}
assert.ok(!breakdown.includes("Total del depósito"));
assert.ok(!breakdown.includes("Solo se muestran distribuciones realizadas"));
assert.ok(!breakdown.includes("Seleccionar un destino sin conciliarlo no aplica el dinero"));
assert.ok(personalDetail.indexOf('class="cn-detail-table cn-credit-people"') > personalDetail.indexOf("</table>"), "Client detail must follow the summary, not be nested inside it");
const signedTotals = renderCashDistribution({...deposit, credit_balance_usd: 0, destinations: [
    {type: "Créditos", label: "P1", amount_usd: 0.3, people: [
        {client_name: "Ana", amount_usd: 0.1}, {client_name: "Luis", amount_usd: 0.2}]},
    {type: "Partida complementaria", label: "Ajuste", amount_usd: -0.1},
]});
assert.match(signedTotals, /Total resumen<\/th><th class="cn-number">[^<]*0\.20/);
assert.match(signedTotals, /Total detalle por cliente<\/th><th class="cn-number">[^<]*0\.30/);
const emptyTotals = renderCashDistribution({...deposit, destinations: [], credit_balance_usd: 0});
assert.match(emptyTotals, /Total resumen<\/th><th class="cn-number">[^<]*0\.00/);
const clientCredit = {type: "Saldo a favor del cliente", label: "CN-COMP-2026-00537 · Pendiente",
    employer: "AIRTEC S A", amount_usd: 22.15,
    people: [{client_name: "ANA <PEREZ> & LOPEZ", client_number: "00123", amount_usd: 22.15}]};
const creditDeposit = {...deposit, destinations: [clientCredit], credit_balance_usd: 22.15, undetailed_credit_usd: 0};
for (const type of ['Saldo a favor del cliente', 'Saldo a favor de la empresa', 'Partida complementaria']) {
    const markup = renderCashDistribution({...creditDeposit, destinations: [{...clientCredit, type}]});
    assert.ok(!markup.includes('cn-credit-people'), `No client detail table for ${type}`);
    assert.ok(!markup.includes('Distribución por persona'));
    assert.ok(markup.includes('Resumen de distribución'));
    assert.match(markup, /Total resumen<\/th><th class="cn-number">[^<]*22\.15/);
}
const creditSummary = renderCashDistribution(creditDeposit).split('</table>')[0];
assert.ok(creditSummary.includes('Cliente: ANA &lt;PEREZ&gt; &amp; LOPEZ'));
assert.ok(creditSummary.includes('Nro. Cliente: 00123'));
assert.ok(creditSummary.includes('CN-COMP-2026-00537 · Pendiente'));
assert.ok(creditSummary.includes('Empresa: AIRTEC S A'));
assert.ok(!creditSummary.includes('<PEREZ>'));
assert.match(creditSummary, /Total resumen<\/th><th class="cn-number">[^<]*22\.15/);
const missingClient = renderCashDistribution({...creditDeposit, destinations: [{...clientCredit, people: []}]}).split('</table>')[0];
assert.ok(missingClient.includes('Cliente: No disponible'));
assert.ok(missingClient.includes('Nro. Cliente: No disponible'));
const companyCredit = renderCashDistribution({...creditDeposit, destinations: [{...clientCredit, type: 'Saldo a favor de la empresa', people: []}]}).split('</table>')[0];
assert.ok(!companyCredit.includes('Nro. Cliente:'));
assert.ok(!personalDetail.split('</table>')[0].includes('Nro. Cliente:'));
assert.ok(breakdown.includes("Detalle por persona no disponible"));
const bankDetail = renderCashDistribution({...deposit, bank_account: 'BANPRO <3268> & C$'});
assert.ok(bankDetail.includes('Cuenta bancaria'));
assert.ok(bankDetail.includes('BANPRO &lt;3268&gt; &amp; C$'));
assert.ok(!bankDetail.includes('BANPRO <3268>'));
assert.ok(breakdown.includes('Sin cuenta asignada'));
const bankCard = renderCashCard({...deposit, bank_account: 'BANPRO <3268> & C$'});
const managedCard = renderCashCard({...deposit, settled: true, credit_management_pending_usd: 75});
assert.match(managedCard, /Conciliado con saldo a favor/);
assert.match(managedCard, /Pendiente de gestión.*75\.00/);
const clientCard = renderCashCard({...deposit, credit_balance_usd: 22.15, client_credit_usd: 22.15,
    company_credit_usd: 0, undetailed_credit_usd: 0});
assert.match(clientCard, /Saldo a favor del cliente:.*22\.15/);
assert.ok(!clientCard.includes('Saldo a favor de la empresa:'));
assert.ok(!clientCard.includes('Saldo a favor sin detalle:'));
const companyCard = renderCashCard({...deposit, credit_balance_usd: 1.29, client_credit_usd: 0,
    company_credit_usd: 1.29, undetailed_credit_usd: 0});
assert.match(companyCard, /Saldo a favor de la empresa:.*1\.29/);
assert.ok(!companyCard.includes('Saldo a favor del cliente:'));
const mixedCard = renderCashCard({...deposit, credit_balance_usd: 23.44, client_credit_usd: 22.15,
    company_credit_usd: 1.29, undetailed_credit_usd: 0});
assert.match(mixedCard, /Saldo a favor del cliente:.*22\.15/);
assert.match(mixedCard, /Saldo a favor de la empresa:.*1\.29/);
assert.ok(!mixedCard.includes('Saldo a favor:'));
assert.match(renderCashCard(deposit), /Saldo a favor sin detalle:.*100\.00/);
const partialIdentity = renderCashCard({...deposit, client_credit_usd: 20});
assert.match(partialIdentity, /Saldo a favor del cliente:.*20\.00/);
assert.match(partialIdentity, /Saldo a favor sin detalle:.*80\.00/);
assert.ok(!partialIdentity.includes('Saldo a favor de la empresa:'));
assert.match(renderCashCard({...deposit, settled: false, needs_review: false}), /Por revisar/);
assert.ok(bankCard.includes('Cuenta bancaria: BANPRO &lt;3268&gt; &amp; C$'));
assert.ok(!bankCard.includes('BANPRO <3268>'));
assert.ok(renderCashCard(deposit).includes('Cuenta bancaria: Sin cuenta asignada'));
assert.ok(breakdown.includes("P-MARZO") && breakdown.includes("P-ABRIL") && breakdown.includes("Cobranza administrativa"));
assert.ok(breakdown.includes("20/05/2025"));
assert.ok(breakdown.includes("&lt;DEP&amp;1&gt;") && !breakdown.includes("<DEP&1>"));
assert.ok(breakdown.includes("NIO") && breakdown.includes("36,624.30"));
assert.ok(breakdown.includes("no significa que ya fue reembolsado"));
assert.ok(renderCashCard(deposit).includes("Distribuido entre varios períodos"));
assert.ok(renderMonthSummary(data.periods, "E", "2025-04", []).includes("Aplicado / Asignado"));
assert.ok(!renderMonthSummary(data.periods, "E", "2025-04", []).includes("Depósitos recibidos en el mes"));
assert.ok(renderMonthSummary([], "E", "2025-05", [deposit]).includes("Sin período de cobranza este mes"));
const clientReceipt = {...deposit, name: 'CLIENT-DEP', employer: 'E', month: '2025-04',
    credit_balance_usd: 22.15, client_credit_usd: 22.15, company_credit_usd: 0, undetailed_credit_usd: 0};
const companyReceipt = {...deposit, name: 'COMPANY-DEP', employer: 'E', month: '2025-04',
    credit_balance_usd: 1.29, client_credit_usd: 0, company_credit_usd: 1.29, undetailed_credit_usd: 0};
const clientMonth = renderMonthSummary(data.periods, 'E', '2025-04', [clientReceipt]);
assert.match(clientMonth, /Saldo a favor del cliente:.*22\.15/);
assert.ok(!clientMonth.includes('Saldo a favor de la empresa:'));
const companyMonth = renderMonthSummary(data.periods, 'E', '2025-04', [companyReceipt]);
assert.match(companyMonth, /Saldo a favor de la empresa:.*1\.29/);
assert.ok(!companyMonth.includes('Saldo a favor del cliente:'));
const mixedMonth = renderMonthSummary(data.periods, 'E', '2025-04', [clientReceipt, companyReceipt, clientReceipt]);
assert.match(mixedMonth, /Saldo a favor del cliente:.*22\.15/);
assert.match(mixedMonth, /Saldo a favor de la empresa:.*1\.29/);
assert.ok(!mixedMonth.includes('Saldo a favor:'));
assert.ok(!mixedMonth.includes('Saldo a favor sin detalle:'));
assert.equal(summarizeCash([clientReceipt, companyReceipt, clientReceipt]).client_credit_usd, 22.15);
assert.equal(summarizeCash([clientReceipt, companyReceipt, clientReceipt]).company_credit_usd, 1.29);
assert.equal(summarizeCash([{name: 'A', client_credit_usd: .1}, {name: 'B', client_credit_usd: .2}]).client_credit_usd, .3);
assert.match(renderMonthSummary([], 'E', '2025-04', [deposit]), /Saldo a favor sin detalle:.*100\.00/);
const original = JSON.stringify(data);
(async () => {
    context.frappe.pages["control-credinomina"].on_page_load({});
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(html.includes('data-month="2025-04"'));
    assert.ok(html.includes('data-month="2025-05"'));
    assert.ok(html.includes('data-month="2025-06"'));
    assert.ok(html.includes("SOLO-DEPOSITOS"));
    const openDeposits = html.match(/<details[^>]*data-section="open_deposits"[\s\S]*?<\/details>/)[0];
    assert.doesNotMatch(openDeposits, /<th>Distribución<\/th>|DESGLOSE-NO-VISIBLE/);
    assert.equal((openDeposits.match(/<th>/g) || []).length, 9);
    assert.equal((openDeposits.match(/<td(?:\s|>)/g) || []).length, 9, "Header and row cells remain aligned");
    assert.match(openDeposits, /data-remittance="D1">REF-ABIERTA/);
    assert.match(openDeposits, /Distribuido US\$/);
    assert.match(openDeposits, /800\.00/);
    handlers["[data-month]"].call({"data-employer": "E", "data-month": "2025-05"});
    const month = dialog;
    assert.equal(month.options.animate, false, "Month navigation must not race Bootstrap modal transitions");
    assert.ok(month.shown && month.html.includes('data-cash-deposit="D1"'));
    assert.ok(!month.html.includes('data-cash-deposit="D2"'));
    assert.ok(!month.html.includes('data-month-period="P-ABRIL"'));
    assert.ok(month.html.includes("No hay períodos de cobranza en este mes"));
    assert.ok(month.html.indexOf("Períodos de cobranza del mes") < month.html.indexOf("Depósitos recibidos en el mes"));
    assert.match(month.html, /Períodos de cobranza del mes · [^<]*0\.00<\/h4>/);
    month.events["[data-cash-deposit]"].call({"data-cash-deposit": "D1"});
    assert.equal(month.shown, false);
    assert.equal(dialog.options.title, "Distribución del depósito");
    assert.equal(dialog.options.size, "extra-large", "Deposit distribution uses the wider responsive dialog");
    assert.ok(dialog.shown && dialog.html.includes("P-ABRIL"));
    const distribution = dialog;
    assert.equal(distribution.options.animate, false, "Immediate return must hide the deposit dialog synchronously");
    distribution.options.secondary_action();
    assert.ok(month.shown && !distribution.shown);
    distribution.options.primary_action();
    assert.deepEqual(route, ["Form", "CN Remittance Allocation", "D1"]);
    assert.equal(JSON.stringify(data), original);
    console.log("OK: receipts by actual month, no-period companies, unique full totals, deposit cards, multi-period distribution, fees, credit balances and navigation.");
})().catch(error => {console.error(error); process.exitCode = 1;});
