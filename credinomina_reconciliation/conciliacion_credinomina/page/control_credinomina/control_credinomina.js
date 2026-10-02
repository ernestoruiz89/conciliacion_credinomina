(function () {
const MONEY_EPSILON = 0.005;
frappe.pages["control-credinomina"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("Control de Credinómina"),
        single_column: true,
    });
    const currentYear = new Date().getFullYear();
    let controlsReady = false;
    let updatingYearOptions = false;
    const yearField = page.add_field({
        fieldname: "year",
        fieldtype: "Select",
        label: __("Año"),
        options: ["Todos", String(currentYear)].join("\n"),
        default: String(currentYear),
        change: () => refresh(),
    });
    const employerField = page.add_field({
        fieldname: "employer",
        fieldtype: "Link",
        label: __("Empresa"),
        options: "CN Employer",
        change: () => refresh(),
    });
    const $root = $('<div class="cn-control"></div>').appendTo(page.main);
    $root.html(`${styles()}<div class="cn-loading">${esc(__("Cargando control..."))}</div>`);
    let currentData = null;
    let workLimit = 100;
    let summaryMode = true;
    let calendarYear = currentYear;
    let allYearsSelected = false;
    let refreshSequence = 0;
    let navigationSequence = 0;
    const detailRequests = new Map();

    function refresh() {
        // Frappe may trigger change while initial controls/options are built.
        if (!controlsReady || updatingYearOptions) return Promise.resolve();
        const requestedYear = yearField.get_value();
        const requestedEmployer = employerField.get_value() || null;
        const sequence = ++refreshSequence;
        navigationSequence++;
        detailRequests.clear();
        return frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_data",
            args: {
                year: requestedYear,
                employer: requestedEmployer,
            },
            freeze: true,
            freeze_message: __("Actualizando control..."),
        }).then((response) => {
            if (sequence !== refreshSequence || requestedYear !== yearField.get_value() || requestedEmployer !== (employerField.get_value() || null)) return;
            currentData = response.message || { periods: [], totals: {}, open_deposits: [], work_items: [] };
            const allYears = currentData.year === "Todos";
            if (allYears && !allYearsSelected) calendarYear = currentYear;
            if (allYears && ![...(currentData.periods || []), ...(currentData.cash_deposits || [])].some(p => String(p.month).startsWith(`${calendarYear}-`))) calendarYear = currentYear;
            allYearsSelected = allYears;
            const years = [...new Set([currentYear, ...(currentData.available_years || []),
                ...(requestedYear !== "Todos" ? [Number(requestedYear)] : [])])].filter(Number.isFinite).sort((a, b) => b - a);
            updatingYearOptions = true;
            try {
                yearField.df.options = ["Todos", ...years.map(String)].join("\n");
                yearField.refresh();
                yearField.set_input(requestedYear);
            } finally { updatingYearOptions = false; }
            render(currentData);
        }).catch(() => {
            if (sequence !== refreshSequence) return;
            $root.html(`${styles()}<div class="cn-empty">${esc(__("No se pudo cargar el control."))}</div>`);
        });
    }

    function render(data) {
        const periods = data.periods || [];
        const totals = data.totals || {};
        const workItems = data.work_items || [];
        const workCount = data.work_item_count ?? workItems.length;
        const allYears = data.year === "Todos";
        const year = allYears ? calendarYear : Number(data.year || currentYear);
        const calendarPeriods = periods.filter(period => String(period.month).slice(0, 4) === String(year));
        const cashDeposits = data.cash_deposits || [];
        const calendarDeposits = cashDeposits.filter(deposit => String(deposit.month).slice(0, 4) === String(year));
        const calendarYears = [...new Set([currentYear, ...[...periods, ...cashDeposits].map(period => Number(String(period.month).slice(0, 4)))])]
            .filter(Number.isFinite).sort((a, b) => b - a);
        const calendarFilter = allYears ? `<label class="cn-summary-toggle cn-calendar-filter">${esc(__("Año del calendario"))}
            <select class="form-control input-sm" data-calendar-year aria-label="${esc(__("Año del calendario"))}">
                ${calendarYears.map(value => `<option value="${value}" ${value === year ? "selected" : ""}>${value}</option>`).join("")}
            </select></label>` : "";
        const companies = [...new Map([...calendarDeposits, ...calendarPeriods].map((period) => [period.employer, {
            id: period.employer,
            name: period.employer_name || period.employer,
        }])).values()].sort((a, b) => a.name.localeCompare(b.name));
        const byCell = new Map();
        calendarPeriods.forEach((period) => {
            const key = `${period.employer}|${period.month}`;
            if (!byCell.has(key)) byCell.set(key, []);
            byCell.get(key).push(period);
        });
        const cashByCell = new Map();
        calendarDeposits.forEach(deposit => {
            const key = `${deposit.employer}|${deposit.month}`;
            if (!cashByCell.has(key)) cashByCell.set(key, []);
            cashByCell.get(key).push(deposit);
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
            [__("Depósitos asignados"), totals.remitted_usd, "remitted"],
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
        const overdueCount = data.overdue_count ?? workItems.filter((item) => item.kind === "overdue_exception").length;
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
        const workFooter = workCount > visibleWork.length ? `
            <div class="cn-work-more"><span>${visibleWork.length} ${esc(__("de"))} ${workCount} ${esc(__("gestiones"))}</span>
            <button type="button" class="btn btn-default btn-sm" data-more-work>${esc(__("Mostrar más"))}</button></div>` : "";
        const matrixRows = companies.map((company) => `
            <tr>
                <th class="cn-company">${esc(company.name)}</th>
                ${months.map((month) => {
                    const cellPeriods = byCell.get(`${company.id}|${month.key}`) || [];
                    const receipts = cashByCell.get(`${company.id}|${month.key}`) || [];
                    if (!cellPeriods.length && !receipts.length) return '<td class="cn-empty-cell">—</td>';
                    const ordered = [...cellPeriods].sort((a, b) =>
                        a.reconciliation_mode === "Historica" && b.reconciliation_mode === "Historica"
                            ? historicalSortDate(a).localeCompare(historicalSortDate(b))
                            : cycleOrder(a.collection_cycle) - cycleOrder(b.collection_cycle)
                    );
                    if (summaryMode || !ordered.length) return renderMonthSummary(ordered, company.id, month.key, receipts);
                    const monthRemitted = ordered.reduce((sum, item) => sum + Number(item.remitted_usd || 0), 0);
                    const monthCompared = ordered.reduce((sum, item) => sum + Number(
                        item.reconciliation_mode === "Historica" ? item.applied_usd || 0 : item.deducted_usd || 0
                    ), 0);
                    const monthState = ordered.every((item) => ["conciliado", "historico_conciliado"].includes(item.control_state)) ? "conciliado" :
                        ordered.some((item) => ["diferencia", "excedente", "historico_excedente", "historico_excepcion"].includes(item.control_state)) ? "diferencia" :
                        ordered.some((item) => ["parcial", "historico_parcial"].includes(item.control_state)) ? "parcial" : "en_transito";
                    return `<td class="cn-cell cn-${esc(ordered.length === 1 ? ordered[0].control_state : monthState)}">
                        ${receipts.length ? `<button type="button" class="cn-cell-button" data-month="${esc(month.key)}" data-employer="${esc(company.id)}">${renderCashSummary(receipts)}</button>` : ""}
                        ${ordered.length > 1 ? `<div class="cn-cell-summary">${esc(__("Total del mes"))}: ${money(monthRemitted)} / ${money(monthCompared)}</div>` : ""}
                        ${ordered.map((period) => `<button type="button" class="cn-cell-button" data-period="${esc(period.name)}">
                            <span class="cn-cell-cycle">${esc(period.reconciliation_mode === "Historica" ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}</span>
                            <span class="cn-cell-amount">${money(period.remitted_usd)} / ${money(period.reconciliation_mode === "Historica" ? period.applied_usd : period.deducted_usd)}</span>
                            <span class="cn-cell-sub">${esc(period.reconciliation_mode === "Historica" ? __("Asignado / aplicado") : __("Asignado / deducido"))}</span>
                            <span class="cn-badge">${esc(stateLabel(period.control_state))}${period.deduction_basis === "Depósito coincidente" ? ` · ${esc(__("Deducción inferida"))}` : ""}</span>
                            ${(period.rounding_movement_count ?? (period.rounding_movements || []).length) ? `<span class="cn-cell-credit">${esc(__("Ajuste menor"))}: ${signedMoney(period.rounding_adjustment_usd)}</span>` : ""}
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
        ` : `<div class="cn-empty">${esc(__("No hay períodos ni depósitos en el año seleccionado."))}</div>`;
        const deposits = data.open_deposits || [];
        const unassigned = data.unassigned_historical_applications || [];
        const unassignedCount = data.unassigned_historical_count ?? unassigned.length;
        const depositCount = data.open_deposit_count ?? deposits.length;
        const moreRows = (section, shown, count) => count > shown ? `<div class="cn-work-more">${shown} ${esc(__("de"))} ${count}
            <button type="button" class="btn btn-default btn-sm" data-more-section="${section}">${esc(__("Mostrar más"))}</button></div>` : "";
        const unassignedTable = unassigned.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Fecha"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Crédito"))}</th><th>${esc(__("Aplicado neto US$"))}</th><th>${esc(__("Motivo"))}</th>
            </tr></thead><tbody>${unassigned.map((row) => `<tr>
                <td>${esc(displayDate(row.event_date))}</td>
                <td><button class="cn-text-link" type="button" data-import="${esc(row.parent)}">${esc(row.reference)}</button></td>
                <td>${esc(row.loan_number)}</td>
                <td class="cn-number">${money(row.net_applied_usd ?? row.amount)}</td>
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
                    <span class="cn-year">${esc(allYears ? __("Todos los años") : String(year))}</span>
                </div>
            </div>
            <section class="cn-panel cn-work-panel"><div class="cn-panel-head"><h3>${esc(__("Qué falta hacer"))}</h3><span>${workCount} ${esc(__("gestiones"))}${overdueCount ? ` · ${overdueCount} ${esc(__("vencidas"))}` : ""}</span></div>${workTable}${workFooter}</section>
            <details class="cn-panel cn-collapsible"><summary>${esc(__("Ver cifras de control"))}</summary><div class="cn-kpis cn-secondary-kpis">${cards}</div></details>
            <section class="cn-panel"><div class="cn-panel-head"><h3>${esc(__("Empresas por mes de conciliación"))}</h3>${calendarFilter}<label class="cn-summary-toggle"><input type="checkbox" data-summary ${summaryMode ? "checked" : ""}> ${esc(__("Resumen"))}</label><span>${year} · ${calendarPeriods.length} ${esc(__("períodos"))}</span></div>${allYears ? `<p class="text-muted">${esc(__("Este selector cambia solo el calendario. Los totales, gestiones y Excel incluyen todos los años."))}</p>` : ""}${matrix}</section>
            ${unassignedCount ? `<details class="cn-panel cn-collapsible" data-section="unassigned_historical_applications"><summary>${esc(__("Aplicaciones históricas sin período"))} · ${unassignedCount}</summary>${unassignedTable}${moreRows("unassigned_historical_applications", unassigned.length, unassignedCount)}</details>` : ""}
            ${employerField.get_value() ? "" : `<details class="cn-panel cn-collapsible" data-section="open_deposits"><summary>${esc(__("Depósitos con saldo a favor o sin asignar"))} · ${depositCount}</summary>${depositTable}${moreRows("open_deposits", deposits.length, depositCount)}</details>`}
            <p class="cn-footnote">${esc(__("CxC a empleados es la parte de la cuota no deducida según el detalle de la empresa; excluye cuotas sin detalle y requiere cotejo con el saldo del core. El deducido sin depósito asignado y los depósitos sin asignar pueden representar el mismo cobro: no los sume ni trate el primero como CxC confirmada. Ningún depósito se aplica automáticamente a un crédito sin identificar su destino. Cifras en US$."))}</p>
        `);
    }

    function loadDetail(kind, name) {
        const key = `${kind}:${name}`;
        if (!detailRequests.has(key)) {
            const request = frappe.call({
                method: `credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_${kind}_detail`,
                args: {[`${kind}_name`]: name},
                freeze: true,
                freeze_message: __("Cargando detalle…"),
            }).then(response => response.message).catch(error => {
                if (detailRequests.get(key) === request) detailRequests.delete(key);
                throw error;
            });
            detailRequests.set(key, request);
        }
        return detailRequests.get(key);
    }

    async function showPeriod(name, parentDialog = null) {
        const period = (currentData?.periods || []).find((item) => item.name === name);
        if (!period) return;
        const dataAtRequest = currentData;
        const navigation = ++navigationSequence;
        if (period.detail_loaded === false) {
            try {
                const detail = await loadDetail("period", name);
                if (currentData !== dataAtRequest || navigation !== navigationSequence) return;
                Object.assign(period, detail);
            } catch (error) {
                if (navigation === navigationSequence && parentDialog) parentDialog.show();
                return;
            }
        }
        const historical = period.reconciliation_mode === "Historica";
        const historicalTable = (period.historical_rows || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Fecha"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Referencia"))}</th>
                <th>${esc(__("Aplicado neto US$"))}</th><th>${esc(__("Depósito asignado US$"))}</th><th>${esc(__("Pendiente US$"))}</th><th>${esc(__("Depósitos"))}</th><th>${esc(__("Excepción"))}</th>
            </tr></thead><tbody>${(period.historical_rows || []).map((row) => `<tr>
                <td>${esc(displayDate(row.event_date))}</td>
                <td>${esc(row.client_number)} · ${esc(row.client_name)} / ${esc(row.loan_number)}</td>
                <td>${esc(row.reference)}</td>
                <td class="cn-number">${money(row.net_applied_usd ?? row.amount)}</td>
                <td class="cn-number">${money(row.historical_remitted_usd)}</td>
                <td class="cn-number">${money(row.historical_balance_usd)}</td>
                <td>${allocationLines(row.historical_detail)}</td>
                <td>${applicationExceptionAction(period, row, currentData.can_create_exception)}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("No hay aplicaciones históricas asignadas."))}</div>`;
        const rowTable = (period.rows || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Cliente"))}</th><th>${esc(__("Crédito / cuota"))}</th><th>${esc(__("Cobranza US$"))}</th><th>${esc(__("Deducido"))}</th><th>${esc(__("CxC empleado US$"))}</th>
                <th>${esc(__("Aplicado"))}</th><th>${esc(__("Complementario"))}</th><th>${esc(__("Depositado"))}</th><th>${esc(__("Ajuste US$"))}</th><th>${esc(__("Depósitos"))}</th><th>${esc(__("Excepción / antecedente"))}</th><th>${esc(__("Estado"))}</th>
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
                <td>${esc(applicationStatusLabel(row.application_status || row.deduction_status))}${row.deduction_status === "Inferida por depósito" ? `<br><span class="cn-inherited-note">${esc(__("Deducción inferida, sin detalle de planilla"))}</span>` : ""}</td>
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
            ...(parentDialog ? {
                secondary_action_label: __("Volver al mes"),
                secondary_action() {
                    dialog.hide();
                    parentDialog.show();
                },
            } : {}),
        });
        dialog.show();
        dialog.get_field("detail").$wrapper.html(`
            <div class="cn-dialog">
                ${renderPeriodRemark(period)}
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
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Depositado"))}</div><div class="cn-kpi-value">${money(period.remitted_usd)}</div></div>
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
        dialog.get_field("detail").$wrapper.on("click", "[data-register-exception]", function () {
            const row = (period.historical_rows || []).find(r => r.name === $(this).attr("data-register-exception"));
            if (!row) return;
            showApplicationException(period, row, async () => {
                dialog.hide();
                await refresh();
                showPeriod(period.name, parentDialog);
            });
        });
    }

    $root.on("change", "[data-summary]", function () {
        summaryMode = this.checked;
        if (currentData) render(currentData);
    });
    $root.on("change", "[data-calendar-year]", function () {
        calendarYear = Number(this.value);
        if (currentData) render(currentData);
    });
    $root.on("click", "[data-month]", function () {
        const employer = $(this).attr("data-employer");
        const month = $(this).attr("data-month");
        const periods = (currentData?.periods || []).filter(item => item.employer === employer && item.month === month)
            .sort((a, b) => a.reconciliation_mode === "Historica" && b.reconciliation_mode === "Historica"
                ? historicalSortDate(a).localeCompare(historicalSortDate(b)) || a.name.localeCompare(b.name)
                : cycleOrder(a.collection_cycle) - cycleOrder(b.collection_cycle) || a.name.localeCompare(b.name));
        const receipts = (currentData?.cash_deposits || []).filter(item => item.employer === employer && item.month === month);
        if (!periods.length && !receipts.length) return;
        const dialog = new frappe.ui.Dialog({title: __("Períodos del mes"), size: "extra-large",
            fields: [{fieldname: "periods", fieldtype: "HTML"}]});
        dialog.get_field("periods").$wrapper.html(`
            <p>${esc(periods[0]?.employer_name || employer)} · ${esc(month)}</p>
            ${currentData.cash_deposits == null ? "" : renderCashPanel(receipts)}
            <h4>${esc(__("Períodos de cobranza del mes"))}</h4>
            <p>${esc(periods.length ? __("El resumen suma todos estos períodos; seleccione uno para ver su detalle.") : __("No hay períodos de cobranza en este mes. Los depósitos recibidos pueden cubrir meses anteriores."))}</p>
            <div class="cn-period-cards">${periods.map(renderPeriodCard).join("")}</div>`);
        dialog.get_field("periods").$wrapper.on("click", "[data-cash-deposit]", async function () {
            const deposit = receipts.find(item => item.name === $(this).attr("data-cash-deposit"));
            if (!deposit) return;
            const dataAtRequest = currentData;
            const navigation = ++navigationSequence;
            if (deposit.detail_loaded === false) {
                try {
                    const detail = await loadDetail("deposit", deposit.name);
                    if (currentData !== dataAtRequest || navigation !== navigationSequence) return;
                    Object.assign(deposit, detail);
                } catch (error) { return; }
            }
            dialog.hide();
            showCashDeposit(deposit, dialog);
        });
        dialog.get_field("periods").$wrapper.on("click", "[data-month-period]", function () {
            dialog.hide();
            showPeriod($(this).attr("data-month-period"), dialog);
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
    $root.on("click", "[data-more-work]", async function () {
        if ((currentData?.work_item_count || 0) > (currentData?.work_items || []).length) {
            await loadMoreRows("work_items");
            return;
        }
        workLimit += 100;
        if (currentData) render(currentData);
    });
    const sectionRequests = new Set();
    async function loadMoreRows(section) {
        if (!currentData || sectionRequests.has(section)) return;
        sectionRequests.add(section);
        const dataAtRequest = currentData;
        const sequence = refreshSequence;
        try {
            const response = await frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_rows",
                args: {section, year: yearField.get_value(), employer: employerField.get_value() || null,
                    start: (currentData[section] || []).length},
                freeze: true, freeze_message: __("Cargando más registros…"),
            });
            if (dataAtRequest !== currentData || sequence !== refreshSequence) return;
            currentData[section] = [...(currentData[section] || []), ...(response.message?.rows || [])];
            const countField = {work_items: "work_item_count", open_deposits: "open_deposit_count",
                unassigned_historical_applications: "unassigned_historical_count"}[section];
            currentData[countField] = response.message.count;
            if (section === "work_items") workLimit += 100;
            render(currentData);
            if (section !== "work_items") $root.find(`[data-section="${section}"]`).prop("open", true);
        } finally { sectionRequests.delete(section); }
    }
    $root.on("click", "[data-more-section]", function () { loadMoreRows($(this).attr("data-more-section")); });
    $root.on("click", "[data-import]", function () {
        frappe.set_route("Form", "CN Accounting Import", $(this).attr("data-import"));
    });
    $root.on("click", "[data-remittance]", function () {
        frappe.set_route("Form", "CN Remittance Allocation", $(this).attr("data-remittance"));
    });
    $root.on("click", "[data-movement]", function () {
        frappe.set_route("Form", "CN Complementary Item", $(this).attr("data-movement"));
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
    page.set_primary_action(__("Actualizar"), refresh);
    controlsReady = true;
    refresh();
};

function applicationExceptionAction(period, row, canCreate) {
    if (row.exception_name) return `<button type="button" class="btn btn-default btn-xs" data-exception="${esc(row.exception_name)}" title="${esc(row.exception_status || "")}">${esc(__("Ver excepción"))}</button>`;
    if (!(Number(row.historical_balance_usd) > MONEY_EPSILON) || !canCreate) return "—";
    if (period.status === "Cerrado") return `<span class="text-muted">${esc(__("Período cerrado"))}</span>`;
    return `<button type="button" class="btn btn-default btn-xs" data-register-exception="${esc(row.name)}">${esc(__("Registrar excepción"))}</button>`;
}

async function showApplicationException(period, row, onSaved) {
    await frappe.model.with_doctype("CN Reconciliation Exception");
    let saving = false;
    const dialog = new frappe.ui.Dialog({
        title: __("Registrar excepción"), size: "large",
        fields: [
            {fieldname: "usd_currency", fieldtype: "Data", default: "USD", hidden: 1},
            {fieldname: "context", fieldtype: "HTML", options: `<p>${esc(period.employer_name || period.employer)} · ${esc(period.name)}</p><p class="text-muted">${esc(__("Registra el caso para seguimiento. No aplica depósitos ni elimina el saldo pendiente."))}</p>`},
            {fieldname: "client_name", fieldtype: "Data", label: __("Cliente"), default: row.client_name, read_only: 1},
            {fieldname: "client_number", fieldtype: "Data", label: __("Nro. Cliente"), default: row.client_number, read_only: 1},
            {fieldtype: "Column Break"},
            {fieldname: "loan_number", fieldtype: "Data", label: __("Nro. Crédito"), default: row.loan_number, read_only: 1},
            {fieldname: "reference", fieldtype: "Data", label: __("Referencia"), default: row.reference, read_only: 1},
            {fieldtype: "Section Break", label: __("Diferencia identificada")},
            {fieldname: "applied_usd", fieldtype: "Currency", options: "usd_currency", label: __("Aplicado neto US$"), default: row.net_applied_usd ?? row.amount, read_only: 1, precision: 2},
            {fieldtype: "Column Break"},
            {fieldname: "assigned_usd", fieldtype: "Currency", options: "usd_currency", label: __("Depósito asignado US$"), default: row.historical_remitted_usd, read_only: 1, precision: 2},
            {fieldtype: "Column Break"},
            {fieldname: "pending_usd", fieldtype: "Currency", options: "usd_currency", label: __("Pendiente US$"), default: row.historical_balance_usd, read_only: 1, precision: 2},
            {fieldtype: "Section Break"},
            {fieldname: "cause_category", fieldtype: "Select", label: __("Causa"), reqd: 1, default: "Por determinar",
                options: frappe.meta.get_docfield("CN Reconciliation Exception", "cause_category").options},
            {fieldname: "description", fieldtype: "Small Text", label: __("Descripción de la excepción"), reqd: 1},
        ],
        primary_action_label: __("Registrar excepción"),
        primary_action: async values => {
            if (saving) return;
            saving = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                const response = await frappe.call({
                    method: "credinomina_reconciliation.control_exceptions.create_application_exception",
                    args: {period_name: period.name, application_id: row.name,
                        expected_pending_usd: row.historical_balance_usd,
                        cause_category: values.cause_category, description: values.description},
                    freeze: true, freeze_message: __("Registrando excepción…"),
                });
                dialog.hide();
                frappe.show_alert({message: response.message.created ? __("Excepción registrada.") : __("La aplicación ya tiene una excepción. Use Ver excepción."), indicator: "green"});
                await onSaved();
            } finally {
                saving = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
    });
    dialog.show();
}

function renderPeriodCard(period) {
    const historical = period.reconciliation_mode === "Historica";
    return `<button type="button" class="cn-period-card cn-${esc(period.control_state || "en_transito")}" data-month-period="${esc(period.name)}">
        <span class="cn-period-card-name">${esc(period.name)}</span>
        <span class="cn-cell-cycle">${esc(historical ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}</span>
        <span class="cn-badge">${esc(stateLabel(period.control_state))}</span>
        ${renderPeriodRemark(period)}
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

function renderPeriodRemark(period) {
    const remark = String(period.remark || "").trim();
    if (!remark) return "";
    return `<span class="cn-period-remark"><strong>${esc(__("Observaciones"))}</strong><span>${esc(remark)}</span></span>`;
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

function renderMonthSummary(periods, employer, month, receipts = []) {
    const total = summarizeMonth(periods);
    const compared = total.historical ? total.applied_usd : total.deducted_usd;
    if (!periods.length) return `<td class="cn-cell"><button type="button" class="cn-cell-button" data-employer="${esc(employer)}" data-month="${esc(month)}">${renderCashSummary(receipts)}<span class="cn-cell-sub">${esc(__("Sin período de cobranza este mes"))}</span><span class="cn-cell-sub">${esc(__("Ver depósitos"))} →</span></button></td>`;
    return `<td class="cn-cell cn-${esc(total.state)}"><button type="button" class="cn-cell-button" data-employer="${esc(employer)}" data-month="${esc(month)}">
        <span class="cn-cell-cycle">${esc(__("Mes completo"))} · ${total.count} ${esc(__("períodos"))}</span>
        <span class="cn-cell-amount">${money(total.remitted_usd)}${total.historical || total.operative ? ` / ${money(compared)}` : ""}</span>
        <span class="cn-cell-sub">${esc(total.historical ? __("Asignado / aplicado") : total.operative ? __("Asignado / deducido") : __("Depósitos asignados"))}</span>
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
        ${renderCashSummary(receipts)}
        <span class="cn-cell-sub">${esc(__("Ver períodos del mes"))}</span>
    </button></td>`;
}

function summarizeCash(receipts) {
    const unique = [...new Map(receipts.map(deposit => [deposit.name, deposit])).values()];
    const sum = field => unique.reduce((cents, deposit) => cents + Math.round(Number(deposit[field] || 0) * 100), 0) / 100;
    return {count: unique.length, settled: unique.filter(d => d.settled).length,
        review: unique.filter(d => d.needs_review).length,
        creditCount: unique.filter(d => Number(d.credit_balance_usd) > MONEY_EPSILON).length,
        ...Object.fromEntries(["total_usd", "credits_usd", "other_usd", "adjustments_usd", "credit_balance_usd", "unclassified_usd", "review_usd"].map(field => [field, sum(field)]))};
}

function renderCashSummary(receipts) {
    if (!receipts.length) return "";
    const total = summarizeCash(receipts);
    return `<span class="cn-cash-summary">
        <span class="cn-cell-sub">${esc(__("Depósitos recibidos en el mes"))} · ${total.count}</span>
        <strong class="cn-cell-amount">${money(total.total_usd)}</strong>
        <span class="cn-cell-sub">${total.settled} ${esc(__("conciliados"))}${total.review ? ` · <strong class="cn-cash-review">${total.review} ${esc(__("por revisar"))}</strong>` : ""}${total.creditCount ? ` · ${total.creditCount} ${esc(__("con saldo a favor"))}` : ""}</span>
        ${Math.abs(total.other_usd) > MONEY_EPSILON ? `<span class="cn-cell-sub">${esc(__("Otros conceptos"))}: ${signedMoney(total.other_usd)}</span>` : ""}
        ${total.credit_balance_usd > MONEY_EPSILON ? `<span class="cn-cell-credit">${esc(__("Saldo a favor"))}: ${money(total.credit_balance_usd)}</span>` : ""}
        ${Math.abs(total.unclassified_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin identificar"))}: ${signedMoney(total.unclassified_usd)}</span>` : ""}
    </span>`;
}

function cashStatus(deposit) {
    return deposit.needs_review ? __("Por revisar") : deposit.settled ? __("Conciliado") : __("Saldo a favor documentado");
}

function renderCashPanel(receipts) {
    const unique = [...new Map(receipts.map(deposit => [deposit.name, deposit])).values()];
    return `<section class="cn-cash-panel"><h4>${esc(__("Depósitos recibidos en el mes"))} · ${money(summarizeCash(unique).total_usd)}</h4>
        <p class="text-muted">${esc(__("Por fecha del depósito, aunque paguen períodos anteriores. No se suman a las asignaciones de cobranza mostradas abajo."))}</p>
        ${unique.length ? `<div class="cn-period-cards cn-cash-cards">${unique.map(renderCashCard).join("")}</div>` : `<p>${esc(__("No hay depósitos confirmados recibidos en este mes."))}</p>`}
    </section>`;
}

function renderCashCard(deposit) {
    const state = deposit.needs_review ? "diferencia" : deposit.settled ? "conciliado" : "excedente";
    return `<button type="button" class="cn-period-card cn-${state}" data-cash-deposit="${esc(deposit.name)}">
        <span class="cn-period-card-name">${esc(deposit.reference || deposit.name)}</span>
        <span class="cn-cell-sub">${esc(displayDate(deposit.date))} · ${esc(deposit.name)}</span>
        <span class="cn-cell-sub">${esc(__("Cuenta bancaria"))}: ${esc(deposit.bank_account || __("Sin cuenta asignada"))}</span>
        <strong class="cn-cell-amount">${money(deposit.total_usd)}</strong>
        <span class="cn-badge">${esc(cashStatus(deposit))}</span>
        <span class="cn-cell-sub">${esc(__("A créditos"))}: ${money(deposit.credits_usd)}</span>
        ${Math.abs(Number(deposit.other_usd)) > MONEY_EPSILON ? `<span class="cn-cell-sub">${esc(__("Otros conceptos"))}: ${signedMoney(deposit.other_usd)}</span>` : ""}
        ${Number(deposit.credit_balance_usd) > MONEY_EPSILON ? `<span class="cn-cell-credit">${esc(__("Saldo a favor"))}: ${money(deposit.credit_balance_usd)}</span>` : ""}
        ${Math.abs(Number(deposit.unclassified_usd)) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin identificar"))}: ${signedMoney(deposit.unclassified_usd)}</span>` : ""}
        ${deposit.shared ? `<span class="cn-cell-sub">${esc(__("Distribuido entre varios períodos"))}</span>` : ""}
        <span class="cn-period-card-open">${esc(__("Ver distribución del depósito"))} →</span>
    </button>`;
}

function showCashDeposit(deposit, parentDialog) {
    const dialog = new frappe.ui.Dialog({
        title: __("Distribución del depósito"), size: "extra-large",
        fields: [{fieldname: "distribution", fieldtype: "HTML"}],
        primary_action_label: __("Abrir depósito"),
        primary_action() { dialog.hide(); frappe.set_route("Form", "CN Remittance Allocation", deposit.name); },
        secondary_action_label: __("Volver al mes"),
        secondary_action() { dialog.hide(); if (parentDialog) parentDialog.show(); },
    });
    dialog.get_field("distribution").$wrapper.html(renderCashDistribution(deposit));
    dialog.show();
}

function renderCashDistribution(deposit) {
    const lines = (deposit.destinations || []).map(item => ({...item}));
    for (const [field, type, label] of [
        ["credit_balance_usd", __("Saldo a favor"), __("Pendiente de gestión; no aplicado a créditos")],
        ["unclassified_usd", __("Sin identificar"), __("Pendiente de asignar o justificar")],
        ["review_usd", __("Distribución por revisar"), __("El importe asignado no coincide con su desglose")],
    ]) if (Math.abs(Number(deposit[field] || 0)) > MONEY_EPSILON) lines.push({type, label, amount_usd: deposit[field]});
    const original = new Intl.NumberFormat("es-NI", {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(deposit.original_amount || 0));
    const summaryTotal = cashTableTotal(lines);
    return `<div class="cn-cash-distribution">
        <div class="cn-kpis cn-cash-kpis">
            <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Referencia del depósito"))}</div><div class="cn-kpi-value">${esc(deposit.reference || deposit.name)}</div><div class="cn-cash-kpi-note">${esc(displayDate(deposit.date))}<br>${esc(deposit.name)}</div></div>
            <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Cuenta bancaria"))}</div><div class="cn-kpi-value">${esc(deposit.bank_account || __("Sin cuenta asignada"))}</div>${deposit.currency ? `<div class="cn-cash-kpi-note">${esc(__("Original"))}: ${esc(deposit.currency)} ${original}</div>` : ""}</div>
            <div class="cn-kpi cn-kpi-remitted"><div class="cn-kpi-label">${esc(__("Depositado total USD"))}</div><div class="cn-kpi-value">${money(deposit.total_usd)}</div></div>
            <div class="cn-kpi cn-cash-result"><div class="cn-kpi-label">${esc(__("Estado de conciliación"))}</div><div class="cn-kpi-value">${esc(deposit.result || __("Pendiente"))}</div></div>
        </div>
        <div class="cn-list-scroll"><table class="cn-detail-table cn-cash-distribution-summary"><caption>${esc(__("Resumen de distribución"))}</caption><thead><tr><th>${esc(__("Destino"))}</th><th>${esc(__("Período / concepto"))}</th><th>${esc(__("Mes de cobranza"))}</th><th>${esc(__("US$"))}</th></tr></thead><tbody>
            ${lines.map(item => `<tr><td>${esc(item.type)}</td><td>${esc(item.label)}${item.employer ? `<div>${esc(__("Empresa"))}: ${esc(item.employer)}</div>` : ""}</td><td>${esc(item.month || "—")}</td><td class="cn-number">${signedMoney(item.amount_usd)}</td></tr>`).join("")}
        </tbody><tfoot><tr><th colspan="3">${esc(__("Total resumen"))}</th><th class="cn-number">${money(summaryTotal)}</th></tr></tfoot></table></div>
        ${lines.map(renderCreditPeople).join("")}
        ${Number(deposit.credit_balance_usd) > MONEY_EPSILON ? `<p class="cn-cell-credit">${esc(__("El saldo a favor requiere seguimiento. Su documentación no significa que ya fue reembolsado."))}</p>` : ""}
    </div>`;
}

function cashTableTotal(rows) {
    return rows.reduce((cents, row) => cents + Math.round(Number(row.amount_usd || 0) * 100), 0) / 100;
}

function renderCreditPeople(destination) {
    if (destination.type !== "Créditos") return "";
    const people = destination.people || [];
    if (!people.length) return `<p class="text-muted">${esc(destination.label)} · ${esc(__("Detalle por persona no disponible"))}</p>`;
    return `<div class="cn-list-scroll"><table class="cn-detail-table cn-credit-people">
        <caption>${esc(__("Distribución por persona"))} · ${esc(destination.label)}</caption>
        <thead><tr><th>${esc(__("Cliente"))}</th><th>${esc(__("Nro. Cliente"))}</th><th>${esc(__("Nro. Crédito"))}</th><th>${esc(__("Asignado US$"))}</th></tr></thead>
        <tbody>${people.map(person => `<tr><td>${esc(person.client_name || __("Cliente no disponible"))}</td><td>${esc(person.client_number || "—")}</td><td>${esc(person.loan_number || "—")}</td><td class="cn-number">${money(person.amount_usd)}</td></tr>`).join("")}</tbody>
        <tfoot><tr><th colspan="3">${esc(__("Total detalle por cliente"))}</th><th class="cn-number">${money(cashTableTotal(people))}</th></tr></tfoot>
    </table></div>`;
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
    // Modality changes the workflow, not the user-facing status vocabulary.
    const normalized = ({
        historico_conciliado: "conciliado", historico_parcial: "parcial",
        historico_pendiente: "en_transito", historico_excedente: "excedente",
        historico_excepcion: "diferencia",
    })[state] || state;
    return ({
        conciliado: __("Conciliado"),
        parcial: __("Parcial"),
        diferencia: __("Con diferencia"),
        excedente: __("Con excedente"),
        pendiente_detalle: __("Sin detalle"),
        en_transito: __("Pendiente"),
    })[normalized] || __("Pendiente");
}

function applicationStatusLabel(status) {
    return ({
        "Aplicado y remitido": __("Aplicado y depositado"),
        "Remitido, aplicacion parcial": __("Depositado, aplicación parcial"),
    })[status] || status;
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
        .cn-panel-head { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid #e2e8f0; }
        .cn-panel-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: #1e293b; }
        .cn-panel-head span { font-size: 11px; color: #64748b; }
        .cn-summary-toggle { display: flex; align-items: center; gap: 6px; margin: 0 12px 0 auto; font-size: 12px; cursor: pointer; }
        .cn-calendar-filter { white-space: nowrap; }
        .cn-calendar-filter select { width: 100px; }
        .cn-period-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 12px; max-height: 60vh; overflow-y: auto; padding: 4px; }
        .cn-period-card { display: flex; flex-direction: column; align-items: flex-start; text-align: left; padding: 16px; border: 1px solid var(--border-color, #e2e8f0); border-radius: 10px; color: var(--text-color, #334155); cursor: pointer; min-width: 0; overflow-wrap: anywhere; }
        .cn-period-card:hover { box-shadow: 0 3px 12px #0f172a18; }
        .cn-period-card:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
        .cn-period-card-name { font-size: 14px; font-weight: 700; margin-bottom: 5px; }
        .cn-cash-summary { display: block; width: 100%; border-top: 1px solid var(--border-color, #cbd5e1); margin-top: 9px; padding-top: 7px; }
        .cn-cash-review { color: #b45309; }
        .cn-cash-panel { border-bottom: 1px solid var(--border-color, #e2e8f0); padding-bottom: 16px; margin-bottom: 16px; }
        .cn-cash-cards { max-height: 40vh; }
        .cn-cash-distribution .cn-badge { padding: 4px 8px; background: var(--control-bg, #f1f5f9); }
        .cn-cash-distribution .cn-cash-kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .cn-cash-kpis .cn-kpi-value { white-space: normal; overflow-wrap: anywhere; }
        .cn-cash-kpi-note { margin-top: 6px; color: var(--text-muted, #64748b); font-size: 12px; overflow-wrap: anywhere; }
        .cn-cash-distribution .cn-list-scroll { margin-bottom: 18px; }
        .cn-cash-distribution caption { caption-side: top; padding: 10px 0; font-weight: 600; color: var(--text-color, #1e293b); }
        .cn-cash-distribution tfoot th { border-top: 2px solid var(--border-color, #e2e8f0); }
        @media(max-width: 900px) { .cn-cash-distribution .cn-cash-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media(max-width: 650px) { .cn-cash-distribution .cn-cash-kpis { grid-template-columns: 1fr; } }
        .cn-period-remark { display: block; width: 100%; margin: 10px 0; padding: 8px 10px; border-left: 2px solid var(--gray-400, #94a3b8); background: var(--control-bg, #f8fafc); border-radius: 4px; font-size: 12px; overflow-wrap: anywhere; }
        .cn-period-remark strong { display: block; margin-bottom: 4px; }
        .cn-period-remark > span { display: block; white-space: pre-wrap; }
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
        .cn-conciliado, .cn-historico_conciliado { background: #ecfdf5; border-left: 3px solid #16a34a !important; }
        .cn-parcial, .cn-historico_parcial { background: #eff6ff; border-left: 3px solid #2563eb !important; }
        .cn-diferencia, .cn-historico_excepcion { background: #fffbeb; border-left: 3px solid #d97706 !important; }
        .cn-en_transito, .cn-historico_pendiente { background: #fff7ed; border-left: 3px solid #f97316 !important; }
        .cn-excedente, .cn-historico_excedente { background: #faf5ff; border-left: 3px solid #7c3aed !important; }
        .cn-pendiente_detalle { background: #f8fafc; border-left: 3px solid #64748b !important; }
        .cn-empty-cell { text-align: center; padding: 28px 4px !important; color: #cbd5e1; }
        .cn-empty, .cn-loading { padding: 24px; text-align: center; color: #94a3b8; font-size: 12px; }
        .cn-detail-table { width: 100%; border-collapse: collapse; font-size: 11px; }
        .cn-detail-table th { text-align: left; background: #f8fafc; color: #64748b; text-transform: uppercase; font-size: 9px; letter-spacing: .04em; }
        .cn-detail-table th, .cn-detail-table td { padding: 9px 10px; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
        .cn-detail-table td:nth-child(3) { white-space: normal; min-width: 100px; }
        .cn-exception-table td:nth-child(7) { white-space: normal; min-width: 180px; }
        .cn-detail-table .cn-number { text-align: right; font-weight: 600; }
        /* Month-dialog cards need readable text, not the calendar's 9px labels. */
        .cn-period-card { font-size: 14px; line-height: 1.5; }
        .cn-period-card .cn-cell-sub,
        .cn-period-card .cn-cell-cycle,
        .cn-period-card .cn-cell-gap,
        .cn-period-card .cn-cell-credit,
        .cn-period-card .cn-badge,
        .cn-period-card .cn-period-remark,
        .cn-period-card .cn-period-card-amounts,
        .cn-period-card .cn-period-card-open { font-size: inherit; line-height: inherit; }
        .cn-period-card .cn-period-card-name { font-size: 16px; }
        .cn-period-card .cn-cell-amount,
        .cn-period-card .cn-period-card-amounts strong { font-size: 18px; line-height: 1.4; }
        /* Keep deposit-dialog typography independent from the compact calendar. */
        .cn-cash-distribution { font-size: 13px; line-height: 1.5; }
        .cn-cash-distribution p,
        .cn-cash-distribution .cn-badge,
        .cn-cash-distribution .cn-kpi-label,
        .cn-cash-distribution .cn-cash-kpi-note,
        .cn-cash-distribution .cn-cell-credit,
        .cn-cash-distribution .cn-detail-table,
        .cn-cash-distribution .cn-detail-table th,
        .cn-cash-distribution .cn-detail-table td,
        .cn-cash-distribution caption { font-size: inherit; line-height: inherit; }
        .cn-cash-distribution .cn-kpi-value { font-size: 20px; line-height: 1.3; }
        .cn-cash-distribution .cn-detail-table th { text-transform: none; letter-spacing: normal; }
        .cn-cash-distribution .cn-detail-table tfoot th { font-weight: 700; color: var(--text-color, #1e293b); }
        .cn-text-link { border: 0; background: none; color: #2563eb; font-weight: 600; padding: 0; }
        .cn-footnote { margin-top: 12px; } .cn-dialog { max-height: 70vh; overflow: auto; }
        .cn-dialog h4 { font-size: 13px; font-weight: 700; margin: 20px 0 8px; }
        .cn-dialog-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
        @media(max-width: 1100px) { .cn-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); } }
        @media(max-width: 650px) { .cn-kpis { grid-template-columns: repeat(2, minmax(120px, 1fr)); } .cn-intro { flex-wrap: wrap; } }
    </style>`;
}
})();
