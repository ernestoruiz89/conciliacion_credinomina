(function () {
const MONEY_EPSILON = 0.005;
frappe.pages["control-credinomina"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Control de Credinómina"),
        single_column: true,
    });
    const currentYear = new Date().getFullYear();
    const yearField = page.add_field({
        fieldname: "year",
        fieldtype: "Select",
        label: __("Año"),
        options: Array.from({ length: 8 }, (_, index) => String(currentYear - index)).join("\n"),
        default: String(currentYear),
    });
    const employerField = page.add_field({
        fieldname: "employer",
        fieldtype: "Link",
        label: __("Empresa"),
        options: "CN Employer",
    });
    const $root = $('<div class="cn-control"></div>').appendTo(page.main);
    $root.html(`${styles()}<div class="cn-loading">${esc(__("Cargando control..."))}</div>`);
    let currentData = null;
    let workLimit = 100;
    let summaryMode = true;

    function refresh() {
        frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_data",
            args: {
                year: yearField.get_value(),
                employer: employerField.get_value() || null,
            },
            freeze: true,
            freeze_message: __("Actualizando control..."),
        }).then((response) => {
            currentData = response.message || { periods: [], totals: {}, open_deposits: [], work_items: [] };
            render(currentData);
        }).catch(() => {
            $root.html(`${styles()}<div class="cn-empty">${esc(__("No se pudo cargar el control."))}</div>`);
        });
    }

    function render(data) {
        const periods = data.periods || [];
        const totals = data.totals || {};
        const workItems = data.work_items || [];
        const year = Number(data.year || currentYear);
        const companies = [...new Map(periods.map((period) => [period.employer, {
            id: period.employer,
            name: period.employer_name || period.employer,
        }])).values()].sort((a, b) => a.name.localeCompare(b.name));
        const byCell = new Map();
        periods.forEach((period) => {
            const key = `${period.employer}|${period.month}`;
            if (!byCell.has(key)) byCell.set(key, []);
            byCell.get(key).push(period);
        });
        const months = Array.from({ length: 12 }, (_, index) => {
            const number = String(index + 1).padStart(2, "0");
            return {
                key: `${year}-${number}`,
                label: new Intl.DateTimeFormat("es-NI", { month: "short" }).format(new Date(year, index, 1)),
            };
        });
        const cards = [
            [__("Cobranza enviada"), totals.expected_usd, "sent"],
            [__("Deducción registrada"), totals.deducted_usd, "deducted"],
            [__("Deducción inferida por depósito"), totals.inferred_deduction_usd, "pending"],
            [__("Aplicado al crédito"), totals.applied_usd, "applied"],
            [__("Remitido"), totals.remitted_usd, "remitted"],
            [__("Movimientos de conciliación"), totals.rounding_movement_abs_usd, "pending"],
            [__("CxC a empleados (no deducido)"), totals.worker_gap_usd, "gap"],
            [__("Deducido sin depósito asignado"), totals.employer_gap_usd, "gap"],
            [__("Detalle pendiente"), totals.pending_detail_usd, "pending"],
            [__("Histórico sin depósito"), totals.historical_pending_usd, "pending"],
            [__("Saldo a favor documentado"), totals.documented_credit_usd, "surplus"],
            [__("Depósitos sin asignar"), totals.unclassified_deposit_usd, "unclassified"],
        ].map(([label, value, kind]) => `
            <div class="cn-kpi cn-kpi-${kind}">
                <div class="cn-kpi-label">${esc(label)}</div>
                <div class="cn-kpi-value">${money(value)}</div>
            </div>
        `).join("");
        const visibleWork = workItems.slice(0, workLimit);
        const overdueCount = workItems.filter((item) => item.kind === "overdue_exception").length;
        const workTable = visibleWork.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table cn-work-table"><thead><tr>
                <th>${esc(__("Prioridad"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Período"))}</th>
                <th>${esc(__("Qué falta"))}</th><th>${esc(__("Importe US$"))}</th><th>${esc(__("Siguiente acción"))}</th><th></th>
            </tr></thead><tbody>${visibleWork.map((item, index) => `<tr>
                <td><span class="cn-work-priority cn-work-priority-${Number(item.priority)}">${esc(item.priority === 0 ? __("Vencida") : item.priority <= 1 ? __("Revisar") : item.priority <= 2 ? __("Pendiente") : __("Seguimiento"))}</span></td>
                <td>${esc(item.employer_name)}</td>
                <td>${esc(item.period_label)}${item.control_cut_on ? `<br><span class="cn-cut-note">${esc(__("Corte registrado"))}: ${esc(displayDate(item.control_cut_on))}</span>` : ""}</td>
                <td>${esc(item.summary)}${item.count ? `<br><span class="cn-work-context">${Number(item.count)} ${esc(__("registros"))}</span>` : ""}${item.due_date ? `<br><span class="cn-work-overdue">${esc(__("Compromiso"))}: ${esc(displayDate(item.due_date))}</span>` : ""}</td>
                <td class="cn-number">${item.amount_usd == null ? "—" : money(item.amount_usd)}</td>
                <td>${esc(item.next_action)}</td>
                <td><button class="cn-text-link" type="button" data-work="${index}">${esc(__("Abrir"))}</button></td>
            </tr>`).join("")}</tbody></table></div>
        ` : `<div class="cn-empty">${esc(__("No hay gestiones pendientes detectadas con la evidencia cargada."))}</div>`;
        const workFooter = workItems.length > visibleWork.length ? `
            <div class="cn-work-more"><span>${visibleWork.length} ${esc(__("de"))} ${workItems.length} ${esc(__("gestiones"))}</span>
            <button type="button" class="btn btn-default btn-sm" data-more-work>${esc(__("Mostrar más"))}</button></div>` : "";
        const matrixRows = companies.map((company) => `
            <tr>
                <th class="cn-company">${esc(company.name)}</th>
                ${months.map((month) => {
                    const cellPeriods = byCell.get(`${company.id}|${month.key}`) || [];
                    if (!cellPeriods.length) return '<td class="cn-empty-cell">—</td>';
                    const ordered = [...cellPeriods].sort((a, b) =>
                        a.reconciliation_mode === "Historica" && b.reconciliation_mode === "Historica"
                            ? historicalSortDate(a).localeCompare(historicalSortDate(b))
                            : cycleOrder(a.collection_cycle) - cycleOrder(b.collection_cycle)
                    );
                    if (summaryMode) return renderMonthSummary(ordered, company.id, month.key);
                    const monthRemitted = ordered.reduce((sum, item) => sum + Number(item.remitted_usd || 0), 0);
                    const monthCompared = ordered.reduce((sum, item) => sum + Number(
                        item.reconciliation_mode === "Historica" ? item.applied_usd || 0 : item.deducted_usd || 0
                    ), 0);
                    const monthState = ordered.every((item) => ["conciliado", "historico_conciliado"].includes(item.control_state)) ? "conciliado" :
                        ordered.some((item) => ["diferencia", "excedente", "historico_excedente", "historico_excepcion"].includes(item.control_state)) ? "diferencia" :
                        ordered.some((item) => ["parcial", "historico_parcial"].includes(item.control_state)) ? "parcial" : "en_transito";
                    return `<td class="cn-cell cn-${esc(ordered.length === 1 ? ordered[0].control_state : monthState)}">
                        ${ordered.length > 1 ? `<div class="cn-cell-summary">${esc(__("Total del mes"))}: ${money(monthRemitted)} / ${money(monthCompared)}</div>` : ""}
                        ${ordered.map((period) => `<button type="button" class="cn-cell-button" data-period="${esc(period.name)}">
                            <span class="cn-cell-cycle">${esc(period.reconciliation_mode === "Historica" ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}</span>
                            <span class="cn-cell-amount">${money(period.remitted_usd)} / ${money(period.reconciliation_mode === "Historica" ? period.applied_usd : period.deducted_usd)}</span>
                            <span class="cn-cell-sub">${esc(period.reconciliation_mode === "Historica" ? __("Depósito / aplicación histórica") : __("Remitido / deducido"))}</span>
                            <span class="cn-badge">${esc(stateLabel(period.control_state))}${period.deduction_basis === "Depósito coincidente" ? ` · ${esc(__("Deducción inferida"))}` : ""}</span>
                            ${(period.rounding_movements || []).length ? `<span class="cn-cell-credit">${esc(__("Ajuste menor"))}: ${signedMoney(period.rounding_adjustment_usd)}</span>` : ""}
                            ${Number(period.historical_pending_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin depósito"))}: ${money(period.historical_pending_usd)}</span>` : ""}
                            ${Number(period.worker_gap_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("CxC empleados"))}: ${money(period.worker_gap_usd)}</span>` : ""}
                            ${Number(period.employer_gap_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin depósito asignado"))}: ${money(period.employer_gap_usd)}</span>` : ""}
                            ${Number(period.documented_credit_usd) > MONEY_EPSILON ? `<span class="cn-cell-credit">${esc(__("Saldo a favor"))}: ${money(period.documented_credit_usd)}</span>` : ""}
                            ${Number(period.unclassified_deposit_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Depósito sin asignar"))}: ${money(period.unclassified_deposit_usd)}</span>` : ""}
                        </button>`).join("")}
                    </td>`;
                }).join("")}
            </tr>
        `).join("");
        const matrix = companies.length ? `
            <div class="cn-matrix-scroll">
                <table class="cn-matrix">
                    <thead><tr><th class="cn-company">${esc(__("Empresa / convenio"))}</th>${months.map((month) => `<th>${esc(month.label)}</th>`).join("")}</tr></thead>
                    <tbody>${matrixRows}</tbody>
                </table>
            </div>
        ` : `<div class="cn-empty">${esc(__("No hay períodos de conciliación en el año seleccionado."))}</div>`;
        const deposits = data.open_deposits || [];
        const unassigned = data.unassigned_historical_applications || [];
        const unassignedTable = unassigned.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Fecha"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Crédito"))}</th><th>${esc(__("Aplicación US$"))}</th><th>${esc(__("Motivo"))}</th>
            </tr></thead><tbody>${unassigned.map((row) => `<tr>
                <td>${esc(displayDate(row.event_date))}</td>
                <td><button class="cn-text-link" type="button" data-import="${esc(row.parent)}">${esc(row.reference)}</button></td>
                <td>${esc(row.loan_number)}</td>
                <td class="cn-number">${money(row.amount)}</td>
                <td>${esc(row.match_reason)}</td>
            </tr>`).join("")}</tbody></table></div>` : "";
        const depositTable = deposits.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table">
                <thead><tr><th>${esc(__("Fecha"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Comprobante"))}</th><th>${esc(__("Depósito original"))}</th><th>${esc(__("Distribuido US$"))}</th><th>${esc(__("Sin distribuir US$"))}</th><th>${esc(__("Saldo a favor documentado US$"))}</th><th>${esc(__("Sin asignar US$"))}</th><th>${esc(__("Distribución"))}</th></tr></thead>
                <tbody>${deposits.map((deposit) => `<tr>
                    <td>${esc(displayDate(deposit.event_date))}</td>
                    <td>${esc(deposit.employer_text)}</td>
                    <td><button class="cn-text-link" type="button" ${deposit.source_doctype === "CN Remittance Allocation" ? `data-remittance="${esc(deposit.parent)}"` : `data-import="${esc(deposit.parent)}"`}>${esc(deposit.reference)}</button></td>
                    <td>${esc(deposit.voucher)}</td>
                    <td class="cn-number">${esc(deposit.amount)} ${esc(deposit.currency)}</td>
                    <td class="cn-number">${money(deposit.allocated_usd)}</td>
                    <td class="cn-number">${money(deposit.unallocated_usd)}</td>
                    <td class="cn-number">${money(deposit.justified_surplus_usd)}</td>
                    <td class="cn-number">${money(deposit.unclassified_usd)}</td>
                    <td>${allocationLines(deposit.allocation_detail)}</td>
                </tr>`).join("")}</tbody>
            </table></div>
        ` : `<div class="cn-empty">${esc(__("No hay depósitos con saldo a favor o sin asignar en este año."))}</div>`;
        $root.html(`${styles()}
            <div class="cn-intro">
                <div><h2>${esc(__("Trabajo de conciliación"))}</h2><p>${esc(__("Priorice la evidencia faltante y abra el documento correspondiente. La matriz mensual queda abajo para consulta."))}</p></div>
                <div class="cn-intro-actions">
                    <button type="button" class="btn btn-default btn-sm" data-export>${esc(__("Exportar Excel"))}</button>
                    <span class="cn-year">${esc(String(year))}</span>
                </div>
            </div>
            <section class="cn-panel cn-work-panel"><div class="cn-panel-head"><h3>${esc(__("Qué falta hacer"))}</h3><span>${workItems.length} ${esc(__("gestiones"))}${overdueCount ? ` · ${overdueCount} ${esc(__("vencidas"))}` : ""}</span></div>${workTable}${workFooter}</section>
            <details class="cn-panel cn-collapsible"><summary>${esc(__("Ver cifras de control"))}</summary><div class="cn-kpis cn-secondary-kpis">${cards}</div></details>
            <section class="cn-panel"><div class="cn-panel-head"><h3>${esc(__("Empresas por mes de conciliación"))}</h3><label class="cn-summary-toggle"><input type="checkbox" data-summary ${summaryMode ? "checked" : ""}> ${esc(__("Resumen"))}</label><span>${periods.length} ${esc(__("períodos"))}</span></div>${matrix}</section>
            ${unassigned.length ? `<details class="cn-panel cn-collapsible"><summary>${esc(__("Aplicaciones históricas sin período"))} · ${unassigned.length}</summary>${unassignedTable}</details>` : ""}
            ${employerField.get_value() ? "" : `<details class="cn-panel cn-collapsible"><summary>${esc(__("Depósitos con saldo a favor o sin asignar"))} · ${deposits.length}</summary>${depositTable}</details>`}
            <p class="cn-footnote">${esc(__("CxC a empleados es la parte de la cuota no deducida según el detalle de la empresa; excluye cuotas sin detalle y requiere cotejo con el saldo del core. El deducido sin depósito asignado y los depósitos sin asignar pueden representar el mismo cobro: no los sume ni trate el primero como CxC confirmada. Ningún depósito se aplica automáticamente a un crédito sin identificar su destino. Cifras en US$."))}</p>
        `);
    }

    function showPeriod(name) {
        const period = (currentData?.periods || []).find((item) => item.name === name);
        if (!period) return;
        const historical = period.reconciliation_mode === "Historica";
        const historicalTable = (period.historical_rows || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Fecha"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Referencia"))}</th>
                <th>${esc(__("Aplicación US$"))}</th><th>${esc(__("Depósito asignado US$"))}</th><th>${esc(__("Sin depósito US$"))}</th><th>${esc(__("Depósitos"))}</th><th>${esc(__("ID para distribución"))}</th>
            </tr></thead><tbody>${(period.historical_rows || []).map((row) => `<tr>
                <td>${esc(displayDate(row.event_date))}</td>
                <td>${esc(row.client_number)} · ${esc(row.client_name)} / ${esc(row.loan_number)}</td>
                <td>${esc(row.reference)}</td>
                <td class="cn-number">${money(row.amount)}</td>
                <td class="cn-number">${money(row.historical_remitted_usd)}</td>
                <td class="cn-number">${money(row.historical_balance_usd)}</td>
                <td>${allocationLines(row.historical_detail)}</td>
                <td>${esc(row.name)}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("No hay aplicaciones históricas asignadas."))}</div>`;
        const rowTable = (period.rows || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Cliente"))}</th><th>${esc(__("Crédito / cuota"))}</th><th>${esc(__("Cobranza US$"))}</th><th>${esc(__("Deducido"))}</th><th>${esc(__("CxC empleado US$"))}</th>
                <th>${esc(__("Aplicado"))}</th><th>${esc(__("Complementario"))}</th><th>${esc(__("Remitido"))}</th><th>${esc(__("Ajuste US$"))}</th><th>${esc(__("Depósitos"))}</th><th>${esc(__("Excepción / antecedente"))}</th><th>${esc(__("Estado"))}</th>
            </tr></thead><tbody>${period.rows.map((row) => `<tr>
                <td>${esc(row.client_number)} · ${esc(row.client_name)}</td>
                <td>${esc(row.loan_number)} / ${esc(row.installment_number)}</td>
                <td class="cn-number">${money(row.expected_usd)}</td>
                <td class="cn-number">${money(row.deducted_usd)}</td>
                <td class="cn-number">${row.employee_receivable_usd == null ? esc(__("Pendiente de detalle")) : money(row.employee_receivable_usd)}</td>
                <td class="cn-number">${money(row.applied_usd)}</td>
                <td class="cn-number">${money(row.complementary_usd)}</td>
                <td class="cn-number">${money(row.remitted_usd)}</td>
                <td class="cn-number">${signedMoney(row.rounding_adjustment_usd)}</td>
                <td>${allocationLines(row.remittance_detail)}</td>
                <td>${row.inherited_exception_comment ? `${esc(__("Trasladada: "))}${esc(row.inherited_exception_comment)}` : row.first_exception_comment ? `${esc(__("Primera conciliación: "))}${esc(row.first_exception_comment)}` : esc(row.application_comment || "—")}</td>
                <td>${esc(row.application_status || row.deduction_status)}${row.deduction_status === "Inferida por depósito" ? `<br><span class="cn-inherited-note">${esc(__("Deducción inferida, sin detalle de planilla"))}</span>` : ""}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("Sin detalle de cobranza."))}</div>`;
        const exceptions = (period.exceptions || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table cn-exception-table"><thead><tr>
                <th>${esc(__("Excepción"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Causa y motivo"))}</th>
                <th>${esc(__("Importe"))}</th><th>${esc(__("Estado"))}</th><th>${esc(__("Responsable"))}</th>
                <th>${esc(__("Próxima acción"))}</th><th>${esc(__("Compromiso"))}</th><th>${esc(__("Soporte"))}</th>
            </tr></thead><tbody>
            ${period.exceptions.map((item) => `<tr>
                <td><button class="cn-text-link" type="button" data-exception="${esc(item.name)}">${esc(item.name)}</button></td>
                <td>${esc(item.client_number)} / ${esc(item.loan_number)}</td>
                <td>${esc(item.cause_category || item.exception_type)}<br>${esc(item.description)}</td>
                <td class="cn-number">${money(item.amount_usd)}</td>
                <td>${esc(item.status)}</td><td>${esc(item.assigned_to || "—")}</td>
                <td>${esc(item.next_action || "—")}</td><td>${esc(displayDate(item.commitment_date) || "—")}</td>
                <td>${esc(item.external_reference || (item.evidence_file ? __("Adjunto") : "—"))}</td>
            </tr>`).join("")}
            </tbody></table></div>` : `<div class="cn-empty">${esc(__("No hay excepciones abiertas."))}</div>`;
        const surplus = (period.surpluses || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr><th>${esc(__("Referencia"))}</th><th>${esc(__("Motivo"))}</th><th>${esc(__("Importe"))}</th><th>${esc(__("Control"))}</th></tr></thead><tbody>
            ${period.surpluses.map((item) => `<tr><td>${esc(item.deposit_reference)}</td><td>${esc(item.reason_type)} · ${esc(item.explanation)}</td><td class="cn-number">${money(item.amount_usd)}</td><td>${esc(item.result)}</td></tr>`).join("")}
            </tbody></table></div>` : `<div class="cn-empty">${esc(__("No hay excedentes documentados."))}</div>`;
        const movements = (period.rounding_movements || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Movimiento"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Aplicación core US$"))}</th><th>${esc(__("Depósito US$"))}</th><th>${esc(__("Diferencia firmada"))}</th><th>${esc(__("Tolerancia"))}</th>
            </tr></thead><tbody>${period.rounding_movements.map((item) => `<tr>
                <td><button class="cn-text-link" type="button" data-movement="${esc(item.name)}">${esc(item.name)}</button></td>
                <td>${esc(item.deposit_reference)}</td>
                <td class="cn-number">${money(item.core_applied_usd)}</td>
                <td class="cn-number">${money(item.deposit_usd)}</td>
                <td class="cn-number">${signedMoney(item.signed_amount_usd)}</td>
                <td class="cn-number">${money(item.tolerance_usd)}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("Sin movimientos de diferencia menor."))}</div>`;
        const dialog = new frappe.ui.Dialog({
            title: `${esc(period.employer_name || period.employer)} · ${esc(period.month)} · ${esc(historical ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}`,
            size: "extra-large",
            fields: [{ fieldname: "detail", fieldtype: "HTML" }],
            primary_action_label: __("Abrir período"),
            primary_action() {
                dialog.hide();
                frappe.set_route("Form", "CN Reconciliation Period", period.name);
            },
        });
        dialog.show();
        dialog.get_field("detail").$wrapper.html(`
            <div class="cn-dialog">
                ${period.control_cut_on ? `<p class="cn-cut-banner">${esc(__("Corte de control registrado"))}: ${esc(displayDate(period.control_cut_on))}. ${esc(__("Los pendientes siguen abiertos y pueden recibir evidencia posterior."))}${period.control_cut_note ? `<br>${esc(period.control_cut_note)}` : ""}</p>` : ""}
                <div class="cn-kpis cn-dialog-kpis">
                    ${historical ? `
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Aplicado al crédito"))}</div><div class="cn-kpi-value">${money(period.applied_usd)}</div></div>
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Depósitos asignados"))}</div><div class="cn-kpi-value">${money(period.remitted_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("Aplicaciones sin depósito"))}</div><div class="cn-kpi-value">${money(period.historical_pending_usd)}</div></div>
                    ` : `
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Enviado"))}</div><div class="cn-kpi-value">${money(period.expected_usd)}</div></div>
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Deducido"))}</div><div class="cn-kpi-value">${money(period.deducted_usd)}</div></div>
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Aplicado"))}</div><div class="cn-kpi-value">${money(period.applied_usd)}</div></div>
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Remitido"))}</div><div class="cn-kpi-value">${money(period.remitted_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("CxC a empleados (no deducido)"))}</div><div class="cn-kpi-value">${money(period.worker_gap_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-pending"><div class="cn-kpi-label">${esc(__("Detalle de empresa pendiente"))}</div><div class="cn-kpi-value">${money(period.pending_detail_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("Deducido sin depósito asignado"))}</div><div class="cn-kpi-value">${money(period.employer_gap_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-surplus"><div class="cn-kpi-label">${esc(__("Saldo a favor"))}</div><div class="cn-kpi-value">${money(period.documented_credit_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("Depósito sin asignar"))}</div><div class="cn-kpi-value">${money(period.unclassified_deposit_usd)}</div></div>
                    `}
                </div>
                ${historical ? `<p>${esc(__("Histórico: no se infieren deducciones de planilla ni CxC a empleados o empresas."))}</p><h4>${esc(__("Aplicaciones contra depósitos"))}</h4>${historicalTable}${(period.exceptions || []).length ? `<h4>${esc(__("Incidencias documentadas"))}</h4>${exceptions}` : ""}` : `<h4>${esc(__("Detalle de cobranza"))}</h4>${rowTable}<h4>${esc(__("Excepciones abiertas"))}</h4>${exceptions}`}
                <h4>${esc(__("Movimientos de conciliación"))}</h4>${movements}
                <h4>${esc(__("Excedentes de depósito"))}</h4>${surplus}
            </div>
        `);
        dialog.get_field("detail").$wrapper.on("click", "[data-exception]", function () {
            dialog.hide();
            frappe.set_route("Form", "CN Reconciliation Exception", $(this).attr("data-exception"));
        });
    }

    $root.on("change", "[data-summary]", function () {
        summaryMode = this.checked;
        if (currentData) render(currentData);
    });
    $root.on("click", "[data-month]", function () {
        const employer = $(this).attr("data-employer");
        const month = $(this).attr("data-month");
        const periods = (currentData?.periods || []).filter(item => item.employer === employer && item.month === month)
            .sort((a, b) => a.reconciliation_mode === "Historica" && b.reconciliation_mode === "Historica"
                ? historicalSortDate(a).localeCompare(historicalSortDate(b)) || a.name.localeCompare(b.name)
                : cycleOrder(a.collection_cycle) - cycleOrder(b.collection_cycle) || a.name.localeCompare(b.name));
        if (!periods.length) return;
        const dialog = new frappe.ui.Dialog({title: __("Períodos del mes"), size: "extra-large",
            fields: [{fieldname: "periods", fieldtype: "HTML"}]});
        dialog.get_field("periods").$wrapper.html(`
            <p>${esc(periods[0].employer_name || employer)} · ${esc(month)}</p>
            <p>${esc(__("El resumen suma todos estos períodos; seleccione uno para ver su detalle."))}</p>
            <div class="cn-period-cards">${periods.map(renderPeriodCard).join("")}</div>`);
        dialog.get_field("periods").$wrapper.on("click", "[data-month-period]", function () {
            dialog.hide();
            showPeriod($(this).attr("data-month-period"));
        });
        dialog.show();
    });
    $root.on("click", "[data-period]", function () { showPeriod($(this).attr("data-period")); });
    $root.on("click", "[data-work]", function () {
        const item = (currentData?.work_items || [])[Number($(this).attr("data-work"))];
        if (item?.target_doctype && item?.target_name) {
            frappe.set_route("Form", item.target_doctype, item.target_name);
        }
    });
    $root.on("click", "[data-more-work]", function () {
        workLimit += 100;
        if (currentData) render(currentData);
    });
    $root.on("click", "[data-import]", function () {
        frappe.set_route("Form", "CN Source Import", $(this).attr("data-import"));
    });
    $root.on("click", "[data-remittance]", function () {
        frappe.set_route("Form", "CN Remittance Allocation", $(this).attr("data-remittance"));
    });
    $root.on("click", "[data-movement]", function () {
        frappe.set_route("Form", "CN Reconciliation Movement", $(this).attr("data-movement"));
    });
    $root.on("click", "[data-export]", downloadControlExcel);
    function downloadControlExcel() {
        const params = new URLSearchParams({ year: yearField.get_value() || String(currentYear) });
        if (employerField.get_value()) params.set("employer", employerField.get_value());
        window.open(
            `/api/method/credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.export_control_excel?${params}`,
            "_blank"
        );
    }
    page.add_button(__("Registrar depósito"), () => frappe.new_doc("CN Remittance Allocation"));
    page.add_button(__("Registrar partida complementaria"), () => frappe.new_doc("CN Complementary Item"));
    page.set_primary_action(__("Actualizar"), refresh);
    refresh();
};

function renderPeriodCard(period) {
    const historical = period.reconciliation_mode === "Historica";
    return `<button type="button" class="cn-period-card cn-${esc(period.control_state || "en_transito")}" data-month-period="${esc(period.name)}">
        <span class="cn-period-card-name">${esc(period.name)}</span>
        <span class="cn-cell-cycle">${esc(historical ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}</span>
        <span class="cn-badge">${esc(stateLabel(period.control_state))}</span>
        <span class="cn-period-card-amounts">
            <span>${esc(__("Aplicado"))}<strong>${money(period.applied_usd)}</strong></span>
            <span>${esc(__("Depósitos asignados"))}<strong>${money(period.remitted_usd)}</strong></span>
            ${!historical ? `<span>${esc(__("Deducido"))}<strong>${money(period.deducted_usd)}</strong></span>` : ""}
        </span>
        ${[["historical_pending_usd", __("Aplicado sin depósito")], ["worker_gap_usd", __("CxC empleados")],
            ["employer_gap_usd", __("Deducido sin depósito asignado")], ["pending_detail_usd", __("Detalle pendiente")]]
            .filter(([field]) => Number(period[field]) > MONEY_EPSILON)
            .map(([field, label]) => `<span class="cn-cell-gap">${esc(label)}: ${money(period[field])}</span>`).join("")}
        <span class="cn-period-card-open">${esc(__("Ver detalle del período"))} →</span>
    </button>`;
}

function summarizeMonth(periods) {
    const sum = field => periods.reduce((cents, period) => cents + Math.round(Number(period[field] || 0) * 100), 0) / 100;
    const states = periods.map(period => period.control_state);
    const state = states.some(value => ["diferencia", "historico_excepcion"].includes(value)) ? "diferencia"
        : states.some(value => ["excedente", "historico_excedente"].includes(value)) ? "excedente"
        : states.every(value => ["conciliado", "historico_conciliado"].includes(value)) ? "conciliado"
        : states.includes("pendiente_detalle") ? "pendiente_detalle"
        : sum("remitted_usd") > MONEY_EPSILON ? "parcial" : "en_transito";
    return {state, count: periods.length,
        historical: periods.every(period => period.reconciliation_mode === "Historica"),
        operative: periods.every(period => period.reconciliation_mode !== "Historica"),
        ...Object.fromEntries(["applied_usd", "deducted_usd", "remitted_usd", "worker_gap_usd",
            "historical_pending_usd", "employer_gap_usd", "pending_detail_usd", "rounding_adjustment_usd"]
            .map(field => [field, sum(field)])),
        inferred: periods.some(period => period.deduction_basis === "Depósito coincidente"),
        // Deposits can relate to several periods: do not sum their surplus here.
        hasDepositBalance: periods.some(period => Number(period.documented_credit_usd) > MONEY_EPSILON || Number(period.unclassified_deposit_usd) > MONEY_EPSILON),
    };
}

function renderMonthSummary(periods, employer, month) {
    const total = summarizeMonth(periods);
    const compared = total.historical ? total.applied_usd : total.deducted_usd;
    return `<td class="cn-cell cn-${esc(total.state)}"><button type="button" class="cn-cell-button" data-employer="${esc(employer)}" data-month="${esc(month)}">
        <span class="cn-cell-cycle">${esc(__("Mes completo"))} · ${total.count} ${esc(__("períodos"))}</span>
        <span class="cn-cell-amount">${money(total.remitted_usd)}${total.historical || total.operative ? ` / ${money(compared)}` : ""}</span>
        <span class="cn-cell-sub">${esc(total.historical ? __("Depósito / aplicación") : total.operative ? __("Remitido / deducido") : __("Depósitos asignados"))}</span>
        ${!total.historical ? `<span class="cn-cell-sub">${esc(__("Aplicado"))}: ${money(total.applied_usd)}</span>` : ""}
        ${!total.historical && !total.operative ? `<span class="cn-cell-sub">${esc(__("Deducido operativo"))}: ${money(total.deducted_usd)}</span>` : ""}
        <span class="cn-badge">${esc(stateLabel(total.state))}</span>
        ${total.inferred ? `<span class="cn-cell-sub">${esc(__("Incluye deducción inferida"))}</span>` : ""}
        ${[["historical_pending_usd", __("Aplicado sin depósito")], ["worker_gap_usd", __("CxC empleados")],
            ["employer_gap_usd", __("Deducido sin depósito asignado")], ["pending_detail_usd", __("Detalle pendiente")]]
            .filter(([field]) => total[field] > MONEY_EPSILON)
            .map(([field, label]) => `<span class="cn-cell-gap">${esc(label)}: ${money(total[field])}</span>`).join("")}
        ${Math.abs(total.rounding_adjustment_usd) > MONEY_EPSILON ? `<span class="cn-cell-credit">${esc(__("Ajuste menor"))}: ${signedMoney(total.rounding_adjustment_usd)}</span>` : ""}
        ${total.hasDepositBalance ? `<span class="cn-cell-credit">${esc(__("Saldos de depósitos: ver períodos"))}</span>` : ""}
        <span class="cn-cell-sub">${esc(__("Ver períodos del mes"))}</span>
    </button></td>`;
}

function money(value) {
    return new Intl.NumberFormat("es-NI", {
        style: "currency", currency: "USD", minimumFractionDigits: 2,
        maximumFractionDigits: 4,
    }).format(Number(value || 0));
}

function signedMoney(value) {
    const amount = Number(value || 0);
    return `${amount > 0 ? "+" : ""}${money(amount)}`;
}

function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
}

function stateLabel(state) {
    return ({
        conciliado: __("Conciliado"),
        parcial: __("Parcial"),
        diferencia: __("Con diferencia"),
        excedente: __("Excedente pendiente"),
        pendiente_detalle: __("Sin detalle"),
        en_transito: __("En tránsito"),
        historico_conciliado: __("Histórico conciliado"),
        historico_parcial: __("Histórico parcial"),
        historico_pendiente: __("Histórico pendiente"),
        historico_excedente: __("Histórico con excedente"),
        historico_excepcion: __("Histórico con excepción"),
    })[state] || __("Pendiente");
}

function cycleOrder(cycle) {
    return ({ "Primera quincena": 1, "Segunda quincena": 2, "Mensual": 3 })[cycle] || 4;
}

function historicalSortDate(period) {
    return period.historical_application_date || period.historical_start_date || period.payroll_month || "";
}

function displayDate(value) {
    return value ? frappe.datetime.str_to_user(String(value).slice(0, 10)) : "";
}

function historicalLabel(period) {
    if (period.historical_scope === "Fecha exacta") {
        return `${__("Histórico")} · ${displayDate(period.historical_application_date)}`;
    }
    if (period.historical_scope === "Rango de fechas") {
        return `${__("Histórico")} · ${displayDate(period.historical_start_date)} – ${displayDate(period.historical_end_date)}`;
    }
    return `${__("Histórico")} · ${__("Mensual")}`;
}

function allocationLines(raw) {
    let entries = [];
    try { entries = JSON.parse(raw || "[]"); } catch (_error) { return "—"; }
    if (!Array.isArray(entries) || !entries.length) return "—";
    return entries.map((entry) => {
        const target = entry.referencia || entry.tipo || "";
        const qualifier = entry.fila_id || entry.partida || entry.destino || "";
        const notes = (entry.excepciones_heredadas || []).map((note) =>
            `<span class="cn-inherited-note">${esc(__("Antecedente"))}: ${esc(note.origin)} · ${money(note.gap_usd)} · ${esc(note.comment)}</span>`
        ).join(" ");
        const value = entry.movimiento
            ? `${signedMoney(entry.diferencia_usd)} (${money(entry.importe_usd)} ${esc(__("efectivo clasificado"))})`
            : money(entry.importe_usd);
        return `${esc(target)} ${esc(qualifier)}: ${value}${notes ? `<br>${notes}` : ""}`;
    }).join("<br>");
}

function styles() {
    return `<style>
        .cn-control { padding: 12px 4px 32px; color: #334155; }
        .cn-intro { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin: 6px 0 18px; }
        .cn-intro-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .cn-intro h2 { font-size: 21px; font-weight: 700; margin: 0 0 4px; color: #1e293b; }
        .cn-intro p, .cn-footnote { font-size: 12px; color: #64748b; margin: 0; }
        .cn-year { background: #dbeafe; border-radius: 8px; padding: 6px 12px; color: #1d4ed8; font-weight: 700; }
        .cn-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 18px; }
        .cn-kpi { min-width: 0; padding: 13px; border: 1px solid #e2e8f0; border-radius: 10px; background: white; box-shadow: 0 2px 6px #0f172a0b; }
        .cn-kpi-label { text-transform: uppercase; letter-spacing: .04em; color: #64748b; font-size: 10px; font-weight: 700; }
        .cn-kpi-value { font-size: 19px; font-weight: 700; color: #1e293b; margin-top: 5px; white-space: nowrap; }
        .cn-kpi-remitted .cn-kpi-value { color: #047857; } .cn-kpi-gap .cn-kpi-value { color: #b45309; }
        .cn-kpi-surplus .cn-kpi-value { color: #7c3aed; }
        .cn-inherited-note { color: #92400e; font-size: 11px; }
        .cn-panel { background: white; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; margin-bottom: 18px; box-shadow: 0 2px 8px #0f172a0a; }
        .cn-panel-head { display: flex; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid #e2e8f0; }
        .cn-panel-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: #1e293b; }
        .cn-panel-head span { font-size: 11px; color: #64748b; }
        .cn-summary-toggle { display: flex; align-items: center; gap: 6px; margin: 0 12px 0 auto; font-size: 12px; cursor: pointer; }
        .cn-period-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 12px; max-height: 60vh; overflow-y: auto; padding: 4px; }
        .cn-period-card { display: flex; flex-direction: column; align-items: flex-start; text-align: left; padding: 16px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 10px; color: var(--text-color, #334155); cursor: pointer; min-width: 0; overflow-wrap: anywhere; }
        .cn-period-card:hover { box-shadow: 0 3px 12px #0f172a18; }
        .cn-period-card:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
        .cn-period-card-name { font-size: 14px; font-weight: 700; margin-bottom: 5px; }
        .cn-period-card-amounts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; width: 100%; margin: 14px 0; font-size: 11px; }
        .cn-period-card-amounts strong { display: block; font-size: 15px; margin-top: 3px; }
        .cn-period-card-open { display: block; margin-top: auto; padding-top: 14px; font-size: 12px; font-weight: 600; color: #2563eb; }
        .cn-work-panel { border-color: #bfdbfe; }
        .cn-work-table td { vertical-align: top; }
        .cn-work-table td:nth-child(3), .cn-work-table td:nth-child(4), .cn-work-table td:nth-child(6) { white-space: normal; min-width: 160px; }
        .cn-work-table td:nth-child(6) { min-width: 220px; }
        .cn-work-priority { display: inline-block; border-radius: 12px; padding: 3px 7px; font-weight: 700; background: #f1f5f9; color: #475569; }
        .cn-work-priority-0 { background: #fee2e2; color: #991b1b; }
        .cn-work-priority-1 { background: #fef3c7; color: #92400e; }
        .cn-work-priority-2 { background: #dbeafe; color: #1e40af; }
        .cn-work-context, .cn-cut-note { color: #64748b; font-size: 10px; }
        .cn-cut-banner { border-left: 3px solid #0ea5e9; background: #f0f9ff; padding: 9px 12px; color: #0c4a6e; font-size: 11px; }
        .cn-work-overdue { color: #b91c1c; font-size: 10px; font-weight: 700; }
        .cn-work-more { display: flex; justify-content: center; align-items: center; gap: 12px; padding: 12px; color: #64748b; font-size: 11px; }
        .cn-collapsible summary { cursor: pointer; padding: 13px 16px; color: #1e293b; font-size: 14px; font-weight: 700; }
        .cn-collapsible[open] summary { border-bottom: 1px solid #e2e8f0; }
        .cn-secondary-kpis { padding: 14px; margin-bottom: 0; }
        .cn-matrix-scroll, .cn-list-scroll { overflow-x: auto; }
        .cn-matrix { width: 100%; min-width: 1500px; border-collapse: separate; border-spacing: 0; table-layout: fixed; }
        .cn-matrix th { background: #1e293b; color: white; padding: 10px 6px; font-size: 11px; text-transform: uppercase; text-align: center; }
        .cn-matrix th.cn-company { width: 200px; text-align: left; position: sticky; left: 0; z-index: 2; }
        .cn-matrix tbody th.cn-company { background: white; color: #334155; border-right: 2px solid #cbd5e1; border-bottom: 1px solid #e2e8f0; text-transform: none; font-size: 12px; }
        .cn-matrix td { border-bottom: 1px solid #e2e8f0; border-right: 1px solid #f1f5f9; vertical-align: top; padding: 0; }
        .cn-cell-button { width: 100%; border: 0; background: transparent; text-align: left; padding: 9px 7px; min-height: 88px; overflow-wrap: anywhere; }
        .cn-cell-button + .cn-cell-button { border-top: 1px dashed #cbd5e1; }
        .cn-cell-cycle { display: block; font-size: 10px; font-weight: 700; color: #475569; margin-bottom: 3px; }
        .cn-cell-summary { padding: 6px 7px; font-size: 10px; font-weight: 700; color: #1e293b; border-bottom: 1px solid #cbd5e1; }
        .cn-cell-button:hover { filter: brightness(.97); } .cn-cell-amount { display: block; font-weight: 700; font-size: 11px; line-height: 1.25; }
        .cn-cell-sub, .cn-cell-gap, .cn-cell-credit { display: block; font-size: 9px; margin-top: 3px; }
        .cn-cell-gap { color: #b45309; font-weight: 700; } .cn-cell-credit { color: #7c3aed; font-weight: 700; }
        .cn-badge { display: inline-block; margin-top: 5px; border-radius: 10px; padding: 2px 5px; background: #ffffffaa; font-size: 9px; font-weight: 700; }
        .cn-conciliado { background: #ecfdf5; border-left: 3px solid #16a34a !important; }
        .cn-parcial { background: #eff6ff; border-left: 3px solid #2563eb !important; }
        .cn-diferencia { background: #fffbeb; border-left: 3px solid #d97706 !important; }
        .cn-en_transito { background: #f8fafc; border-left: 3px solid #94a3b8 !important; }
        .cn-excedente { background: #faf5ff; border-left: 3px solid #7c3aed !important; }
        .cn-pendiente_detalle { background: #f8fafc; border-left: 3px solid #64748b !important; }
        .cn-historico_conciliado { background: #ecfdf5; border-left: 3px solid #059669 !important; }
        .cn-historico_parcial { background: #eff6ff; border-left: 3px solid #3b82f6 !important; }
        .cn-historico_pendiente { background: #fff7ed; border-left: 3px solid #f97316 !important; }
        .cn-historico_excedente { background: #faf5ff; border-left: 3px solid #7c3aed !important; }
        .cn-historico_excepcion { background: #fef2f2; border-left: 3px solid #dc2626 !important; }
        .cn-empty-cell { text-align: center; padding: 28px 4px !important; color: #cbd5e1; }
        .cn-empty, .cn-loading { padding: 24px; text-align: center; color: #94a3b8; font-size: 12px; }
        .cn-detail-table { width: 100%; border-collapse: collapse; font-size: 11px; }
        .cn-detail-table th { text-align: left; background: #f8fafc; color: #64748b; text-transform: uppercase; font-size: 9px; letter-spacing: .04em; }
        .cn-detail-table th, .cn-detail-table td { padding: 9px 10px; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
        .cn-detail-table td:nth-child(3) { white-space: normal; min-width: 100px; }
        .cn-exception-table td:nth-child(7) { white-space: normal; min-width: 180px; }
        .cn-detail-table .cn-number { text-align: right; font-weight: 600; }
        .cn-text-link { border: 0; background: none; color: #2563eb; font-weight: 600; padding: 0; }
        .cn-footnote { margin-top: 12px; } .cn-dialog { max-height: 70vh; overflow: auto; }
        .cn-dialog h4 { font-size: 13px; font-weight: 700; margin: 20px 0 8px; }
        .cn-dialog-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
        @media(max-width: 1100px) { .cn-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); } }
        @media(max-width: 650px) { .cn-kpis { grid-template-columns: repeat(2, minmax(120px, 1fr)); } .cn-intro { flex-wrap: wrap; } }
    </style>`;
}
})();
