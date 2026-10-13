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
    let activeView = "calendar";
    let calendarFullscreen = false, fullscreenController = null;
    let kpisOpen = false, controlKpis = null, kpiRequest = null, kpiError = false;
    let workLimit = 100;
    let workOverviewRequest = null, workOverviewError = false;
    let workFilters = {work_kind: "", responsible: "", due: "", search: "", min_amount: "", sort_by: ""}, workFilterSequence = 0;
    let summaryMode = true;
    let calendarYear = currentYear;
    let calendarScope = "", calendarMonth = "", calendarScroll = null;
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
        workFilters = {work_kind: "", responsible: "", due: "", search: "", min_amount: "", sort_by: ""};
        workFilterSequence++;
        workOverviewRequest = null;
        workOverviewError = false;
        controlKpis = null;
        kpiRequest = null;
        kpiError = false;
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
            if (activeView === "work") loadWorkOverview();
            if (kpisOpen) loadControlKpis();
        }).catch(() => {
            if (sequence !== refreshSequence) return;
            fullscreenController?.exit();
            $root.html(`${styles()}<div class="cn-empty">${esc(__("No se pudo cargar el control."))}</div>`);
        });
    }

    function render(data) {
        const periods = data.periods || [];
        const totals = data.totals || {};
        const workReady = data.work_scope !== "calendar";
        const workItems = workReady ? data.work_items || [] : [];
        const workCount = workReady ? data.work_item_count ?? workItems.length : "…";
        const allYears = data.year === "Todos";
        const year = allYears ? calendarYear : Number(data.year || currentYear);
        const scope = `${year}|${employerField.get_value() || ""}`;
        const oldScroll = $root[0]?.querySelector?.(".cn-matrix-scroll");
        if (scope === calendarScope && oldScroll?.clientWidth) calendarScroll = oldScroll.scrollLeft;
        if (scope !== calendarScope) {
            calendarScope = scope;
            calendarMonth = initialCalendarMonth(data, year);
            calendarScroll = null;
        }
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
        const processFigures = [
            [__("Cobranza enviada"), totals.expected_usd],
            [__("Asignado"), totals.remitted_usd],
            [__("Ajustes de tolerancia (valor absoluto)"), totals.rounding_movement_abs_usd],
            [__("Diferencia cobranza/aplicación (informativo)"), totals.worker_gap_usd],
        ].map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${money(value)}</dd></div>`).join("");
        const visibleWork = workItems.slice(0, workLimit);
        const overdueCount = workReady ? data.overdue_count ?? workItems.filter((item) => item.kind === "overdue_exception").length : 0;
        const workTable = visibleWork.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table cn-work-table"><thead><tr>
                <th>${esc(__("Prioridad"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Período"))}</th>
                <th>${esc(__("Qué falta"))}</th><th>${esc(__("Importe US$"))}</th><th>${esc(__("Siguiente acción"))}</th><th></th>
            </tr></thead><tbody>${visibleWork.map((item, index) => `<tr>
                <td><span class="cn-work-priority cn-work-priority-${Number(item.priority)}">${esc(item.priority === 0 ? __("Vencida") : item.priority <= 1 ? __("Revisar") : item.priority <= 2 ? __("Pendiente") : __("Seguimiento"))}</span></td>
                <td>${esc(item.employer_name)}</td>
                <td>${esc(item.period_label)}${item.period_context ? `<div class="cn-work-context">${esc(__(item.period_context))}</div>` : ""}${item.control_cut_on ? `<br><span class="cn-cut-note">${esc(__("Corte registrado"))}: ${esc(displayDate(item.control_cut_on))}</span>` : ""}</td>
                <td>${esc(item.summary)}${renderWorkClient(item)}${renderWorkException(item)}${item.action_count > 1 ? `<br><strong>${Number(item.action_count)} ${esc(__("acciones pendientes"))}</strong>` : ""}${item.count ? `<br><span class="cn-work-context">${Number(item.count)} ${esc(__("registros"))}</span>` : ""}${item.due_date ? `<br><span class="cn-work-overdue">${esc(__("Compromiso"))}: ${esc(displayDate(item.due_date))}</span>` : ""}</td>
                <td class="cn-number" title="${esc(item.amount_label || item.summary)}">${item.amount_usd == null ? "—" : money(item.amount_usd)}${item.amount_kind || item.action_count > 1 ? `<div class="text-muted">${esc(__(item.amount_kind || "Pendiente principal"))}</div>` : ""}</td>
                <td>${item.action_count > 1 ? `<details><summary>${Number(item.action_count)} ${esc(__("acciones del caso"))}</summary>${item.actions.map(action => `<div class="cn-work-action"><strong>${esc(action.summary)}</strong>${action.amount_usd == null ? "" : ` · ${money(action.amount_usd)}`}<br>${esc(action.next_action)}${action.responsible ? `<br>${esc(__("Responsable"))}: ${esc(action.responsible)}` : ""}</div>`).join("")}<p class="text-muted">${esc(__("Los importes de las acciones no se suman."))}</p></details>` : `${esc(item.next_action)}${item.responsible ? `<br><span class="text-muted">${esc(__("Responsable"))}: ${esc(item.responsible)}</span>` : ""}`}</td>
                <td><button class="cn-text-link" type="button" data-work="${index}">${esc(__(item.action_count > 1 ? "Revisar caso" : item.action_label || "Revisar caso"))}</button></td>
            </tr>`).join("")}</tbody></table></div>
        ` : `<div class="cn-empty">${esc(__("No hay gestiones pendientes detectadas con la evidencia cargada."))}</div>`;
        const workFooter = workCount > visibleWork.length ? `
            <div class="cn-work-more"><span>${visibleWork.length} ${esc(__("de"))} ${workCount} ${esc(__("casos"))}</span>
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
                    const monthTotals = summarizeMonth(ordered);
                    const monthRemitted = monthTotals.remitted_usd;
                    const monthApplied = monthTotals.applied_usd;
                    const monthState = ordered.every((item) => ["conciliado", "historico_conciliado"].includes(item.control_state)) ? "conciliado" :
                        ordered.some((item) => ["diferencia", "excedente", "historico_excedente", "historico_excepcion"].includes(item.control_state)) ? "diferencia" :
                        ordered.some((item) => ["parcial", "historico_parcial"].includes(item.control_state)) ? "parcial" : "en_transito";
                    return `<td class="cn-cell cn-${esc(ordered.length === 1 ? ordered[0].control_state : monthState)}">
                        ${receipts.length ? `<button type="button" class="cn-cell-button" data-month="${esc(month.key)}" data-employer="${esc(company.id)}">${renderCashSummary(receipts)}</button>` : ""}
                        ${ordered.length > 1 ? `<div class="cn-cell-summary">${esc(__("Total del mes"))} · ${esc(__("Aplicado / Asignado"))}: ${money(monthApplied)} / ${money(monthRemitted)}</div>` : ""}
                        ${ordered.map((period) => `<button type="button" class="cn-cell-button" data-period="${esc(period.name)}">
                            <span class="cn-cell-cycle">${esc(period.reconciliation_mode === "Historica" ? historicalLabel(period) : operativeCycleLabel(period))}</span>
                            <span class="cn-cell-amount">${money(periodAmounts(period).applied_usd)} / ${money(periodAmounts(period).remitted_usd)}</span>
                            <span class="cn-cell-sub">${esc(__("Aplicado / Asignado"))}</span>
                            <span class="cn-badge">${esc(stateLabel(period.control_state))}</span>
                            ${(period.rounding_movement_count ?? (period.rounding_movements || []).length) ? `<span class="cn-cell-credit">${esc(__("Complementarias automáticas"))}: ${signedMoney(period.rounding_adjustment_usd)}</span>` : ""}
                            ${Number(period.historical_pending_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin depósito"))}: ${money(period.historical_pending_usd)}</span>` : ""}
                            ${Number(period.worker_gap_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Diferencia cobranza/aplicación"))}: ${money(period.worker_gap_usd)}</span>` : ""}
                            ${renderMissingQualityBasis(period)}
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
                    <thead><tr><th class="cn-company">${esc(__("Empresa / convenio"))}</th>${months.map((month) => `<th data-calendar-column="${month.key}">${esc(month.label)}</th>`).join("")}</tr></thead>
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
                <th>${esc(__("Fecha"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Nombre del cliente"))}</th><th>${esc(__("Crédito"))}</th><th>${esc(__("Aplicado neto US$"))}</th><th>${esc(__("Motivo"))}</th>
            </tr></thead><tbody>${unassigned.map((row) => `<tr>
                <td>${esc(displayDate(row.event_date))}</td>
                <td><button class="cn-text-link" type="button" data-import="${esc(row.parent)}">${esc(row.reference)}</button></td>
                <td>${esc(row.client_name || "—")}</td>
                <td>${esc(row.loan_number)}</td>
                <td class="cn-number">${money(row.net_applied_usd ?? row.amount)}</td>
                <td>${esc(row.match_reason)}</td>
            </tr>`).join("")}</tbody></table></div>` : "";
        const depositTable = deposits.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table">
                <thead><tr><th>${esc(__("Fecha"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Comprobante"))}</th><th>${esc(__("Depósito original"))}</th><th>${esc(__("Distribuido US$"))}</th><th>${esc(__("Sin distribuir US$"))}</th><th>${esc(__("Saldo a favor documentado US$"))}</th><th>${esc(__("Sin asignar US$"))}</th></tr></thead>
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
                </tr>`).join("")}</tbody>
            </table></div>
        ` : `<div class="cn-empty">${esc(__("No hay depósitos con saldo a favor o sin asignar en la consulta."))}</div>`;
        $root.html(`${styles()}
            <div class="cn-intro">
                <div><h2>${esc(__("Control de Credinómina"))}</h2><p>${esc(__("Consulte los meses en Calendario o gestione los pendientes en Trabajo de conciliación."))}</p></div>
                <div class="cn-intro-actions">
                    <button type="button" class="btn btn-default btn-sm" data-export>${esc(__("Exportar Excel"))}</button>
                    <span class="cn-year">${esc(allYears ? __("Todos los años") : String(year))}</span>
                </div>
            </div>
            <details class="cn-panel cn-collapsible" data-control-figures ${kpisOpen ? "open" : ""}>
                <summary data-kpis-toggle>${esc(__("Ver cifras de control"))}</summary>
                <div data-kpi-content>${renderControlKpis(controlKpis, kpiError)}</div>
                <details class="cn-process-figures"><summary>${esc(__("Detalle del proceso"))}</summary>
                    <p>${esc(__("Cifras de los períodos de cobranza seleccionados. Son etapas distintas: no deben sumarse. La cobranza controla calidad; las aplicaciones originan la CxC."))}</p>
                    <dl>${processFigures}</dl>
                </details>
            </details>
            <div class="cn-view-tabs" role="tablist" aria-label="${esc(__("Vistas del control"))}">
                <button type="button" class="cn-view-tab" role="tab" id="cn-control-calendar-tab" aria-controls="cn-control-calendar-panel" data-control-view="calendar" aria-selected="${activeView === "calendar"}" tabindex="${activeView === "calendar" ? "0" : "-1"}">${esc(__("Calendario"))}</button>
                <button type="button" class="cn-view-tab" role="tab" id="cn-control-work-tab" aria-controls="cn-control-work-panel" data-control-view="work" aria-selected="${activeView === "work"}" tabindex="${activeView === "work" ? "0" : "-1"}">${esc(__("Trabajo de conciliación"))}<span class="cn-tab-count">${workCount}</span>${overdueCount ? `<span class="cn-tab-overdue">${overdueCount} ${esc(__("vencidas"))}</span>` : ""}</button>
            </div>
            <div class="cn-tab-panel" role="tabpanel" id="cn-control-calendar-panel" aria-labelledby="cn-control-calendar-tab" data-control-panel="calendar" tabindex="0" ${activeView === "calendar" ? "" : "hidden"}>
                <section class="cn-panel" data-calendar-section><div class="cn-panel-head"><h3>${esc(__("Empresas por mes de conciliación"))}</h3>${calendarFilter}<label class="cn-summary-toggle"><input type="checkbox" data-summary ${summaryMode ? "checked" : ""}> ${esc(__("Resumen"))}</label><span>${year} · ${calendarPeriods.length} ${esc(__("períodos"))}</span><button type="button" class="btn btn-default btn-sm cn-fullscreen-button" data-calendar-fullscreen aria-pressed="${calendarFullscreen}" aria-controls="cn-control-calendar-panel" title="${esc(__("Ampliar calendario / salir con Escape"))}">${esc(__(calendarFullscreen ? "Salir de pantalla completa" : "Pantalla completa"))}</button></div>${allYears ? `<p class="text-muted">${esc(__("Este selector cambia solo el calendario. Los totales, gestiones y Excel incluyen todos los años."))}</p>` : ""}${matrix}</section>
            </div>
            <div class="cn-tab-panel" role="tabpanel" id="cn-control-work-panel" aria-labelledby="cn-control-work-tab" data-control-panel="work" tabindex="0" ${activeView === "work" ? "" : "hidden"}>
                <p class="text-muted">${esc(__("Pendientes de todos los años. El filtro Año se aplica al calendario, las cifras de control y el Excel; no oculta gestiones antiguas en esta pestaña."))}</p>
                ${workReady ? `
                <section class="cn-panel cn-work-panel"><div class="cn-panel-head"><h3>${esc(__("Qué falta hacer"))}</h3><span>${workCount} ${esc(__("casos"))} · ${data.work_action_count ?? workItems.reduce((count, item) => count + (item.action_count || 1), 0)} ${esc(__("acciones"))}${overdueCount ? ` · ${overdueCount} ${esc(__("casos vencidos"))}` : ""}</span></div>
                    ${data.consulted_at ? `<p class="text-muted">${esc(__("Consulta de"))} ${esc(frappe.datetime.str_to_user(data.consulted_at))}. ${esc(__("Actualizar consulta incorpora cambios recientes; esta vista no modifica saldos."))}</p>` : ""}
                    <div class="cn-work-toolbar"><button type="button" class="btn btn-default btn-sm" data-refresh-work>${esc(__("Actualizar consulta"))}</button>
                    <button type="button" class="btn btn-default btn-sm" data-export-work title="${esc(__("Descarga todos los casos que coinciden con los filtros, no solo los visibles en pantalla."))}">${esc(__("Exportar Excel"))}</button></div>
                    <div class="row" style="margin-bottom:16px">
                        <div class="col-sm-4"><label>${esc(__("Tipo de pendiente"))}<select class="form-control" data-work-filter="work_kind">${[["", "Todos"], ["payments", "Pagos pendientes de aplicar"], ["credits", "Saldos a favor"], ["complements", "Partidas complementarias"], ["accounting", "Registro en el core"], ["exceptions", "Excepciones"], ["deposits", "Depósitos"], ["periods", "Aplicaciones y períodos"]].map(([value, label]) => `<option value="${value}" ${workFilters.work_kind === value ? "selected" : ""}>${esc(__(label))}</option>`).join("")}</select></label></div>
                        <div class="col-sm-4"><label>${esc(__("Responsable contiene"))}<input class="form-control" data-work-filter="responsible" value="${esc(workFilters.responsible)}"></label></div>
                        <div class="col-sm-4"><label>${esc(__("Compromiso"))}<select class="form-control" data-work-filter="due">${[["", "Todos"], ["overdue", "Vencidos"], ["upcoming", "Hoy o futuros"], ["undated", "Sin fecha compromiso"]].map(([value, label]) => `<option value="${value}" ${workFilters.due === value ? "selected" : ""}>${esc(__(label))}</option>`).join("")}</select></label></div>
                        <div class="col-sm-6"><label>${esc(__("Cliente, crédito, documento o motivo"))}<input class="form-control" data-work-filter="search" value="${esc(workFilters.search)}"></label></div>
                        <div class="col-sm-3"><label>${esc(__("Importe mínimo US$"))}<input type="number" min="0" step="0.01" class="form-control" data-work-filter="min_amount" value="${esc(workFilters.min_amount)}"></label></div>
                        <div class="col-sm-3"><label>${esc(__("Ordenar por"))}<select class="form-control" data-work-filter="sort_by">${[["", "Prioridad"], ["amount", "Mayor importe"], ["oldest", "Más antiguo"]].map(([value, label]) => `<option value="${value}" ${workFilters.sort_by === value ? "selected" : ""}>${esc(__(label))}</option>`).join("")}</select></label></div>
                    </div>${workTable}${workFooter}</section>
                ${unassignedCount ? `<details class="cn-panel cn-collapsible" data-section="unassigned_historical_applications"><summary>${esc(__("Aplicaciones sin período"))} · ${unassignedCount}</summary>${unassignedTable}${moreRows("unassigned_historical_applications", unassigned.length, unassignedCount)}</details>` : ""}
                ${employerField.get_value() ? "" : `<details class="cn-panel cn-collapsible" data-section="open_deposits"><summary>${esc(__("Depósitos con saldo a favor o sin asignar"))} · ${depositCount}</summary>${depositTable}${moreRows("open_deposits", deposits.length, depositCount)}</details>`}
                ` : `<section class="cn-panel"><p role="status">${esc(__(workOverviewError ? "No se pudieron cargar los pendientes de todos los años." : "Cargando pendientes de todos los años…"))}</p>${workOverviewError ? `<button type="button" class="btn btn-default" data-retry-work>${esc(__("Reintentar"))}</button>` : ""}</section>`}
            </div>
            <p class="cn-footnote">${esc(__("La cobranza sirve para la primera conciliación y no genera cuentas por cobrar. La CxC es lo aplicado en el core menos lo cubierto por depósitos o compensaciones confirmadas vinculadas. El aplicado neto ya incluye los ajustes confirmados; no los descuente dos veces. Los saldos a favor y depósitos sin asignar no reducen automáticamente esa CxC. Cifras en US$."))}</p>
        `);
        const calendarHead = $root[0]?.querySelector?.("[data-calendar-section] .cn-panel-head");
        if (calendarHead) {
            calendarHead.insertAdjacentHTML("beforeend", `<label class="cn-summary-toggle">${esc(__("Ir al mes"))}
                <select class="form-control input-sm" data-calendar-month aria-label="${esc(__("Ir al mes"))}">
                ${months.map(month => `<option value="${month.key}" ${month.key === calendarMonth ? "selected" : ""}>${esc(month.label)}</option>`).join("")}</select></label>`);
            positionCalendar();
        }
    }

    function positionCalendar() {
        const root = $root[0];
        const scroll = root?.querySelector?.(".cn-matrix-scroll");
        if (!scroll?.clientWidth) return;
        const month = root.querySelector(`[data-calendar-column="${calendarMonth}"]`);
        const company = root.querySelector(".cn-matrix thead .cn-company");
        if (calendarScroll == null && month && company) {
            calendarScroll = month.getBoundingClientRect().left - company.getBoundingClientRect().right + scroll.scrollLeft;
        }
        scroll.scrollLeft = Math.max(calendarScroll || 0, 0);
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
        const applicationTable = renderPeriodApplicationTable(period, currentData.can_create_exception);
        const rowTable = renderPeriodCollectionTable(period);
        const exceptions = renderPeriodExceptions(period.exceptions);
        const surplus = (period.surpluses || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr><th>${esc(__("Referencia"))}</th><th>${esc(__("Motivo"))}</th><th>${esc(__("Importe"))}</th><th>${esc(__("Control"))}</th></tr></thead><tbody>
            ${period.surpluses.map((item) => `<tr><td>${esc(item.deposit_reference)}</td><td>${esc(item.reason_type)} · ${esc(item.explanation)}</td><td class="cn-number">${money(item.amount_usd)}</td><td>${esc(item.result)}</td></tr>`).join("")}
            </tbody></table></div>` : `<div class="cn-empty">${esc(__("No hay excedentes documentados."))}</div>`;
        const movements = (period.rounding_movements || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr>
                <th>${esc(__("Partida complementaria"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Categoría"))}</th><th>${esc(__("Importe US$"))}</th>
            </tr></thead><tbody>${period.rounding_movements.map((item) => `<tr>
                <td><button class="cn-text-link" type="button" data-movement="${esc(item.name)}">${esc(item.name)}</button></td>
                <td>${esc(item.deposit_reference)}</td>
                <td>${esc(__("Diferencia por tolerancia"))}</td>
                <td class="cn-number">${signedMoney(item.signed_amount_usd)}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("Sin partidas complementarias automáticas."))}</div>`;
        const dialog = new frappe.ui.Dialog({
            title: `${esc(period.employer_name || period.employer)} · ${esc(period.month)} · ${esc(historical ? historicalLabel(period) : operativeCycleLabel(period))}`,
            size: "extra-large",
            animate: false,
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
        const detailWrapper = dialog.get_field("detail").$wrapper;
        detailWrapper.html(`
            <div class="cn-dialog cn-period-detail">
                ${renderPeriodRemark(period)}
                ${period.control_cut_on ? `<p class="cn-cut-banner">${esc(__("Corte de control registrado"))}: ${esc(displayDate(period.control_cut_on))}. ${esc(__("Los pendientes siguen abiertos y pueden recibir evidencia posterior."))}${period.control_cut_note ? `<br>${esc(period.control_cut_note)}` : ""}</p>` : ""}
                ${historical ? `<div class="cn-kpis cn-dialog-kpis">
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Aplicado al crédito"))}</div><div class="cn-kpi-value">${money(periodAmounts(period).applied_usd)}</div></div>
                    <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Asignado"))}</div><div class="cn-kpi-value">${money(periodAmounts(period).remitted_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("Pendiente US$"))}</div><div class="cn-kpi-value">${money(periodAmounts(period).pending_usd)}</div></div>
                </div>` : renderOperationalSummary(period)}
                ${historical ? `<p>${esc(__("Sin reconstruir deducciones de planilla. La CxC nace de las aplicaciones pendientes de cubrir, igual que en operativo."))}</p><h4>${esc(__("Aplicaciones contra depósitos"))}</h4>${applicationTable}${(period.exceptions || []).length ? `<h4>${esc(__("Incidencias documentadas"))}</h4>${exceptions}` : ""}` : `<h4>${esc(__("Cobranza y aplicación por cliente"))}</h4>${rowTable}<h4>${esc(__("Aplicaciones contra depósitos"))}</h4>${applicationTable}${(period.exceptions || []).length ? `<h4>${esc(__("Excepciones abiertas"))}</h4>${exceptions}` : ""}`}
                ${(period.rounding_movements || []).length ? `<details class="cn-period-secondary"><summary>${esc(__("Partidas complementarias automáticas"))}</summary>${movements}</details>` : ""}
                ${(period.surpluses || []).length ? `<details class="cn-period-secondary"><summary>${esc(__("Saldos a favor documentados"))}</summary>${surplus}</details>` : ""}
            </div>
        `);
        detailWrapper.on("change", "[data-period-difference-filter]", function () {
            applyPeriodDifferenceFilter(detailWrapper[0], this.checked);
        });
        detailWrapper.on("change", "[data-application-difference-filter]", function () {
            applyApplicationDifferenceFilter(detailWrapper[0], this.checked);
        });
        detailWrapper.on("click", "[data-exception]", function () {
            dialog.hide();
            frappe.set_route("Form", "CN Reconciliation Exception", $(this).attr("data-exception"));
        });
        detailWrapper.on("click", "[data-application-import]", function () {
            dialog.hide();
            frappe.set_route("Form", "CN Accounting Import", $(this).attr("data-application-import"));
        });
        detailWrapper.on("click", "[data-register-exception]", function () {
            const row = (period.historical_rows || []).find(r => r.name === $(this).attr("data-register-exception"));
            if (!row) return;
            showApplicationException(period, row, async () => {
                dialog.hide();
                await refresh();
                showPeriod(period.name, parentDialog);
            });
        });
    }

    function loadControlKpis() {
        if (controlKpis) return Promise.resolve();
        if (kpiRequest) return kpiRequest;
        const sequence = refreshSequence;
        kpiError = false;
        $root.find("[data-kpi-content]").html(renderControlKpis(null));
        const pending = Promise.resolve(frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_kpis",
            args: {year: yearField.get_value(), employer: employerField.get_value() || null},
        })).then(response => {
            if (sequence !== refreshSequence) return;
            controlKpis = response.message;
            if (!controlKpis) throw new Error("Missing control figures");
            $root.find("[data-kpi-content]").html(renderControlKpis(controlKpis));
        }).catch(() => {
            if (sequence !== refreshSequence) return;
            kpiError = true;
            $root.find("[data-kpi-content]").html(renderControlKpis(null, true));
        }).finally(() => { if (kpiRequest === pending) kpiRequest = null; });
        kpiRequest = pending;
        return pending;
    }
    $root.on("click", "[data-kpis-toggle]", function () {
        kpisOpen = !kpisOpen;
        if (kpisOpen) return loadControlKpis();
    });
    $root.on("click", "[data-kpis-retry]", loadControlKpis);
    $root.on("click", "[data-kpi-action]", function () {
        if (!controlKpis) return;
        const action = $(this).attr("data-kpi-action");
        if (action === "exceptions" && controlKpis.exceptions?.filters) {
            frappe.route_options = controlKpis.exceptions.filters;
            frappe.set_route("List", "CN Reconciliation Exception");
        } else if ((action === "aging" && controlKpis.applications) ||
                   (action === "receivable-aging" && controlKpis.receivables)) {
            const year = yearField.get_value();
            frappe.route_options = {as_of_date: controlKpis.as_of_date, employer: employerField.get_value() || "",
                balance_type: action === "receivable-aging" ? "CxC por ajustes" : "Aplicado pendiente de depósito", reconciliation_mode: "",
                from_month: year === "Todos" ? "" : `${year}-01-01`, to_month: year === "Todos" ? "" : `${year}-12-01`,
                client_number: "", national_id: "", loan_number: ""};
            frappe.set_route("query-report", "Antiguedad de Saldos");
        }
    });

    $root.on("click", "[data-calendar-fullscreen]", function () {
        if (!fullscreenController) fullscreenController = createCalendarFullscreen($root[0], wrapper, active => {
            calendarFullscreen = active;
            $root.find("[data-calendar-fullscreen]").attr("aria-pressed", String(active))
                .text(__(active ? "Salir de pantalla completa" : "Pantalla completa"));
        });
        return fullscreenController.toggle();
    });
    function selectView(view, focus = false) {
        if (!["calendar", "work"].includes(view)) return;
        if (view !== "calendar") fullscreenController?.exit();
        activeView = view;
        // Toggle existing panels so scrolling, expanded sections and loaded rows survive.
        for (const name of ["calendar", "work"]) {
            const selected = name === view;
            $root.find(`[data-control-view="${name}"]`).attr({"aria-selected": String(selected), tabindex: selected ? "0" : "-1"});
            $root.find(`[data-control-panel="${name}"]`).prop("hidden", !selected);
        }
        if (focus) $root.find(`[data-control-view="${view}"]`).trigger("focus");
        if (view === "work") loadWorkOverview();
        if (view === "calendar") positionCalendar();
    }
    function loadWorkOverview() {
        if (!currentData || (currentData.work_scope !== "calendar" && currentData.work_consultation) || workOverviewRequest) return workOverviewRequest;
        const dataAtRequest = currentData, sequence = refreshSequence;
        workOverviewError = false;
        const request = Promise.resolve(frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_work_overview",
            args: {employer: employerField.get_value() || null},
        })).then(async response => {
            if (currentData !== dataAtRequest || sequence !== refreshSequence) return;
            if (response.message?.work_scope !== "Todos") throw new Error("Incomplete work overview");
            const overview = {...response.message};
            if (Object.values(workFilters).some(Boolean)) {
                const filterSequence = workFilterSequence;
                const {message} = await frappe.call({
                    method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_rows",
                    args: {section: "work_items", year: "Todos", employer: employerField.get_value() || null,
                        start: 0, consultation: overview.consultation, ...workFilters},
                });
                if (currentData !== dataAtRequest || sequence !== refreshSequence || filterSequence !== workFilterSequence) return;
                Object.assign(overview, {work_items: message.rows || [], work_item_count: message.count,
                    work_action_count: message.action_count, overdue_count: message.overdue_count,
                    consultation: message.consultation, consulted_at: message.consulted_at});
            }
            Object.assign(currentData, overview);
            currentData.work_consultation = overview.consultation;
            currentData.open_deposits_consultation = response.message.consultation;
            workLimit = 100;
            render(currentData);
        }).catch(() => {
            if (currentData !== dataAtRequest || sequence !== refreshSequence) return;
            workOverviewError = true;
            render(currentData);
        }).finally(() => {if (workOverviewRequest === request) workOverviewRequest = null;});
        workOverviewRequest = request;
        return request;
    }
    $root.on("click", "[data-retry-work]", () => loadWorkOverview());
    $root.on("click", "[data-refresh-work]", () => {
        if (!currentData) return;
        currentData.work_consultation = null;
        workFilterSequence++;
        loadWorkOverview();
    });
    $root.on("click", "[data-control-view]", function () {
        selectView($(this).attr("data-control-view"));
    });
    $root.on("keydown", "[data-control-view]", function (event) {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const view = event.key === "Home" ? "calendar" : event.key === "End" ? "work"
            : $(this).attr("data-control-view") === "calendar" ? "work" : "calendar";
        selectView(view, true);
    });
    $root.on("change", "[data-summary]", function () {
        summaryMode = this.checked;
        if (currentData) render(currentData);
    });
    $root.on("change", "[data-calendar-year]", function () {
        calendarYear = Number(this.value);
        if (currentData) render(currentData);
    });
    $root.on("change", "[data-calendar-month]", function () {
        calendarMonth = this.value;
        calendarScroll = null;
        positionCalendar();
    });
    $root.on("click", "[data-month]", function () {
        const employer = $(this).attr("data-employer");
        const month = $(this).attr("data-month");
        const periods = (currentData?.periods || []).filter(item => item.employer === employer && item.month === month)
            .sort((a, b) => periodCardDate(a).localeCompare(periodCardDate(b))
                || cycleOrder(a.collection_cycle) - cycleOrder(b.collection_cycle) || a.name.localeCompare(b.name));
        const receipts = (currentData?.cash_deposits || []).filter(item => item.employer === employer && item.month === month)
            .sort((a, b) => String(a.date || "9999-12-31").localeCompare(String(b.date || "9999-12-31"))
                || a.name.localeCompare(b.name));
        if (!periods.length && !receipts.length) return;
        // These dialogs replace/reopen one another. Bootstrap 4 ignores hide/show
        // during a fade, leaving overlapping dialogs/backdrops on rapid navigation.
        const dialog = new frappe.ui.Dialog({title: __("Períodos del mes"), size: "extra-large", animate: false,
            fields: [{fieldname: "periods", fieldtype: "HTML"}]});
        dialog.get_field("periods").$wrapper.html(`
            <p>${esc(periods[0]?.employer_name || employer)} · ${esc(month)}</p>
            <h4>${esc(__("Períodos de cobranza del mes"))} · ${money(summarizeMonth(periods).applied_usd)}</h4>
            <p>${esc(periods.length ? __("Total aplicado en los períodos mostrados; seleccione uno para ver su detalle.") : __("No hay períodos de cobranza en este mes. Los depósitos recibidos pueden cubrir meses anteriores."))}</p>
            <div class="cn-period-cards">${periods.map(renderPeriodCard).join("")}</div>
            ${currentData.cash_deposits == null ? "" : renderCashPanel(receipts)}`);
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
        showWorkCase((currentData?.work_items || []).slice(0, workLimit), Number($(this).attr("data-work")), currentData?.consulted_at);
    });
    $root.on("change", "[data-work-filter]", async function () {
        if (!currentData) return;
        const previousFilters = {...workFilters};
        workFilters[$(this).attr("data-work-filter")] = this.value;
        const sequence = ++workFilterSequence, dataAtRequest = currentData;
        try {
        const {message} = await frappe.call({
            method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_rows",
            args: {section: "work_items", year: "Todos", employer: employerField.get_value() || null, start: 0, consultation: currentData.work_consultation, ...workFilters},
            freeze: true, freeze_message: __("Filtrando todos los pendientes…"),
        });
        if (sequence !== workFilterSequence || currentData !== dataAtRequest) return;
        currentData.work_items = message.rows || [];
        currentData.work_item_count = message.count;
        currentData.work_action_count = message.action_count;
        currentData.overdue_count = message.overdue_count;
        currentData.work_consultation = message.consultation;
        currentData.consulted_at = message.consulted_at;
        workLimit = 100;
        render(currentData);
        } catch (_) {
            if (sequence !== workFilterSequence || currentData !== dataAtRequest) return;
            workFilters = previousFilters;
            render(currentData);
            frappe.msgprint(__("No se pudieron aplicar los filtros. Se conserva la consulta anterior; inténtelo nuevamente."));
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
        const filterSequence = workFilterSequence;
        try {
            const response = await frappe.call({
                method: "credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.get_control_rows",
                args: {section, year: "Todos", employer: employerField.get_value() || null,
                    start: (currentData[section] || []).length, consultation: currentData[section === "work_items" ? "work_consultation" : "open_deposits_consultation"], ...(section === "work_items" ? workFilters : {})},
                freeze: true, freeze_message: __("Cargando más registros…"),
            });
            if (dataAtRequest !== currentData || sequence !== refreshSequence || (section === "work_items" && filterSequence !== workFilterSequence)) return;
            currentData[section] = [...(response.message?.restarted ? [] : currentData[section] || []), ...(response.message?.rows || [])];
            if (response.message.consultation) currentData[section === "work_items" ? "work_consultation" : "open_deposits_consultation"] = response.message.consultation;
            if (response.message.restarted) frappe.show_alert({message: __("La consulta cambió o venció; se muestra la primera página actualizada."), indicator: "blue"});
            const countField = {work_items: "work_item_count", open_deposits: "open_deposit_count",
                unassigned_historical_applications: "unassigned_historical_count"}[section];
            currentData[countField] = response.message.count;
            if (section === "work_items") {
                workLimit = currentData.work_items.length;
                currentData.work_action_count = response.message.action_count;
                currentData.overdue_count = response.message.overdue_count;
                currentData.consulted_at = response.message.consulted_at;
            }
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
    $root.on("click", "[data-export-work]", downloadWorkItemsExcel);
    function downloadWorkItemsExcel() {
        const params = new URLSearchParams();
        if (employerField.get_value()) params.set("employer", employerField.get_value());
        Object.entries(workFilters).forEach(([key, value]) => {
            if (value !== "" && value != null) params.set(key, value);
        });
        if (currentData?.work_consultation) params.set("consultation", currentData.work_consultation);
        window.open(
            `/api/method/credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina.export_work_items_excel?${params}`,
            "_blank"
        );
    }
    page.add_button(__("Registrar depósito"), () => frappe.new_doc("CN Remittance Allocation"));
    page.add_button(__("Consultar corte histórico"), () => frappe.prompt([
        {fieldname: "cutoff_date", fieldtype: "Date", label: __("Fecha de corte"), reqd: 1,
            default: frappe.datetime.get_today(),
            description: __("Consulta saldos por fecha efectiva con los vínculos vigentes. No modifica conciliaciones.")},
        {fieldname: "employer", fieldtype: "Link", label: __("Empresa"), options: "CN Employer",
            default: employerField.get_value() || ""},
    ], values => {
        frappe.route_options = {view_mode: "Resumen", cutoff_date: values.cutoff_date,
            employer: values.employer || "", from_date: "", to_date: ""};
        frappe.set_route("query-report", "Estado de Cuenta por Empresa");
    }, __("Consultar corte histórico"), __("Consultar")));
    page.set_primary_action(__("Actualizar"), refresh);
    controlsReady = true;
    refresh();
};

function initialCalendarMonth(data, year, today = new Date()) {
    const prefix = `${year}-`;
    const active = [...new Set([...(data.periods || []), ...(data.cash_deposits || [])]
        .map(row => String(row.month || "")).filter(month => month.startsWith(prefix)))].sort();
    const current = `${year}-${String(today.getMonth() + 1).padStart(2, "0")}`;
    return Number(year) === today.getFullYear() && (!active.length || active.includes(current))
        ? current : active[0] || `${year}-01`;
}

function showWorkCase(items, index, consultedAt) {
    if (!items[index]) return;
    const dialog = new frappe.ui.Dialog({title: __("Revisar pendientes"), size: "large",
        fields: [{fieldname: "case_detail", fieldtype: "HTML"}],
        primary_action_label: __("Volver a la bandeja"), primary_action() {dialog.hide();}});
    const wrapper = dialog.get_field("case_detail").$wrapper;
    const render = () => {
        const item = items[index];
        wrapper.html(`<div class="d-flex justify-content-between mb-3">
            <button type="button" class="btn btn-default btn-sm" data-case-step="-1" ${index === 0 ? "disabled" : ""}>${esc(__("Anterior"))}</button>
            <strong>${index + 1} / ${items.length} ${esc(__("casos cargados"))}</strong>
            <button type="button" class="btn btn-default btn-sm" data-case-step="1" ${index === items.length - 1 ? "disabled" : ""}>${esc(__("Siguiente"))}</button></div>
            <h4>${esc(item.employer_name || item.employer || "")}</h4><p>${esc(item.period_label || "")}</p>
            ${renderWorkClient(item)}
            ${(item.actions || [item]).map(action => `<div class="border rounded p-3 mb-3">
                <strong>${esc(action.summary)}</strong>${renderWorkException(action)}<p>${action.amount_usd == null ? "—" : money(action.amount_usd)} · ${esc(__(action.amount_kind || "Importe del caso"))}</p>
                <p>${esc(action.next_action)}</p>
                ${action.responsible ? `<div>${esc(__("Responsable"))}: ${esc(action.responsible)}</div>` : ""}
                ${action.due_date ? `<div>${esc(__("Compromiso"))}: ${esc(displayDate(action.due_date))}</div>` : ""}
                ${action.target_doctype && action.target_name ? `<a target="_blank" rel="noopener noreferrer" href="${esc(frappe.utils.get_form_link(action.target_doctype, action.target_name))}">${esc(__("Abrir documento en otra pestaña"))} · ${esc(action.target_name)}</a>` : ""}
            </div>`).join("")}
            <p class="text-muted">${esc(__("Los importes de las acciones no se suman. La bandeja conserva sus filtros; después de gestionar un caso use Actualizar consulta."))}</p>
            ${consultedAt ? `<p class="text-muted">${esc(__("Consulta"))}: ${esc(displayDate(consultedAt))}</p>` : ""}`);
    };
    wrapper.on("click", "[data-case-step]", function () {
        index = Math.max(0, Math.min(items.length - 1, index + Number($(this).attr("data-case-step"))));
        render();
    });
    render();
    dialog.show();
    return dialog;
}

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

function periodAmounts(period) {
    return {...period, applied_usd: period.financial_applied_usd ?? period.applied_total_usd ?? period.applied_usd,
        remitted_usd: period.financial_covered_usd ?? period.remitted_total_usd ?? period.remitted_usd,
        pending_usd: period.financial_pending_usd ?? period.pending_usd ?? period.historical_pending_usd ?? period.employer_receivable_usd};
}

function renderPeriodCard(period) {
    period = periodAmounts(period);
    const historical = period.reconciliation_mode === "Historica";
    return `<button type="button" class="cn-period-card cn-${esc(period.control_state || "en_transito")}" data-month-period="${esc(period.name)}">
        <span class="cn-period-card-name">${esc(period.name)}</span>
        <span class="cn-cell-cycle">${esc(historical ? historicalLabel(period) : operativeCycleLabel(period))}</span>
        <span class="cn-badge">${esc(stateLabel(period.control_state))}</span>
        ${Number(period.open_exception_count || (period.exceptions || []).length) ? `<span class="cn-cell-sub">${esc(__("Excepciones abiertas"))}: ${Number(period.open_exception_count || period.exceptions.length)}</span>` : ""}
        ${renderPeriodRemark(period)}
        ${renderMissingQualityBasis(period)}
        <span class="cn-period-card-amounts">
            <span>${esc(__("Aplicado"))}<strong>${money(period.applied_usd)}</strong></span>
            <span>${esc(__("Asignado"))}<strong>${money(period.remitted_usd)}</strong></span>
        </span>
        ${[["pending_usd", __("Pendiente US$")], ["worker_gap_usd", __("Diferencia cobranza/aplicación")]]
            .filter(([field]) => Number(period[field]) > MONEY_EPSILON)
            .map(([field, label]) => `<span class="cn-cell-gap">${esc(label)}: ${money(period[field])}</span>`).join("")}
        <span class="cn-period-card-open">${esc(__("Ver detalle del período"))} →</span>
    </button>`;
}

function renderMissingQualityBasis(period) {
    const count = Number(period.quality_missing_basis_count || 0);
    return count ? `<span class="cn-cell-gap">${esc(__("Cobranza sin equivalente US$"))}: ${count} · ${esc(__("Revisar base de comparación"))}</span>` : "";
}

function renderPeriodRemark(period) {
    const remark = String(period.remark || "").trim();
    if (!remark) return "";
    return `<span class="cn-period-remark"><strong>${esc(__("Observaciones"))}</strong><span>${esc(remark)}</span></span>`;
}

function summarizeMonth(periods) {
    periods = periods.map(periodAmounts);
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
        ...Object.fromEntries(["applied_usd", "remitted_usd", "worker_gap_usd",
            "historical_pending_usd", "pending_usd", "rounding_adjustment_usd", "quality_missing_basis_count"]
            .map(field => [field, sum(field)])),
        // Deposits can relate to several periods: do not sum their surplus here.
        hasDepositBalance: periods.some(period => Number(period.documented_credit_usd) > MONEY_EPSILON || Number(period.unclassified_deposit_usd) > MONEY_EPSILON),
    };
}

function renderMonthSummary(periods, employer, month, receipts = []) {
    const total = summarizeMonth(periods);
    if (!periods.length) return `<td class="cn-cell"><button type="button" class="cn-cell-button" data-employer="${esc(employer)}" data-month="${esc(month)}">${renderCashSummary(receipts)}<span class="cn-cell-sub">${esc(__("Sin período de cobranza este mes"))}</span><span class="cn-cell-sub">${esc(__("Ver depósitos"))} →</span></button></td>`;
    return `<td class="cn-cell cn-${esc(total.state)}"><button type="button" class="cn-cell-button" data-employer="${esc(employer)}" data-month="${esc(month)}">
        <span class="cn-cell-cycle">${esc(__("Períodos registrados en el mes"))} · ${total.count}</span>
        <span class="cn-cell-amount">${money(total.applied_usd)} / ${money(total.remitted_usd)}</span>
        <span class="cn-cell-sub">${esc(__("Aplicado / Asignado"))}</span>
        <span class="cn-badge">${esc(stateLabel(total.state))}</span>
        ${renderMissingQualityBasis(total)}
        ${[["pending_usd", __("Pendiente US$")], ["worker_gap_usd", __("Diferencia cobranza/aplicación")]]
            .filter(([field]) => total[field] > MONEY_EPSILON)
            .map(([field, label]) => `<span class="cn-cell-gap">${esc(label)}: ${money(total[field])}</span>`).join("")}
        ${Math.abs(total.rounding_adjustment_usd) > MONEY_EPSILON ? `<span class="cn-cell-credit">${esc(__("Complementarias automáticas"))}: ${signedMoney(total.rounding_adjustment_usd)}</span>` : ""}
        ${total.hasDepositBalance ? `<span class="cn-cell-credit">${esc(__("Saldos de depósitos: ver períodos"))}</span>` : ""}
        ${renderCashSummary(receipts)}
        <span class="cn-cell-sub">${esc(__("Ver períodos del mes"))}</span>
    </button></td>`;
}

function summarizeCash(receipts) {
    const unique = [...new Map(receipts.map(deposit => [deposit.name, deposit])).values()];
    const sum = field => unique.reduce((cents, deposit) => cents + Math.round(Number(deposit[field] || 0) * 100), 0) / 100;
    return {count: unique.length, settled: unique.filter(d => d.settled).length,
        review: unique.filter(d => d.needs_review || !d.settled).length,
        creditCount: unique.filter(d => Number(d.credit_balance_usd) > MONEY_EPSILON).length,
        ...Object.fromEntries(["total_usd", "credits_usd", "other_usd", "adjustments_usd", "credit_balance_usd", "client_credit_usd", "company_credit_usd", "unclassified_usd", "review_usd"].map(field => [field, sum(field)]))};
}

function renderCashSummary(receipts) {
    if (!receipts.length) return "";
    const total = summarizeCash(receipts);
    return `<span class="cn-cash-summary">
        <span class="cn-cell-sub">${esc(__("Depósitos recibidos en el mes"))} · ${total.count}</span>
        <strong class="cn-cell-amount">${money(total.total_usd)}</strong>
        <span class="cn-cell-sub">${total.settled} ${esc(__("conciliados"))}${total.review ? ` · <strong class="cn-cash-review">${total.review} ${esc(__("por revisar"))}</strong>` : ""}${total.creditCount ? ` · ${total.creditCount} ${esc(__("con saldo a favor"))}` : ""}</span>
        ${Math.abs(total.other_usd) > MONEY_EPSILON ? `<span class="cn-cell-sub">${esc(__("Otros conceptos"))}: ${signedMoney(total.other_usd)}</span>` : ""}
        ${renderCashCreditBalances(total)}
        ${renderCashReceivables(receipts)}
        ${Math.abs(total.unclassified_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin identificar"))}: ${signedMoney(total.unclassified_usd)}</span>` : ""}
    </span>`;
}

function cashStatus(deposit) {
    return deposit.needs_review || !deposit.settled ? __("Por revisar")
        : Number(deposit.credit_balance_usd) > MONEY_EPSILON ? __("Conciliado con saldo a favor") : __("Conciliado");
}

function renderCashReceivables(receipts) {
    const rows = [...new Map(receipts.flatMap(deposit => deposit.receivable_entries || [])
        .map(row => [`${row.name}|${row.employer}`, row])).values()];
    if (rows.some(row => row.amount_usd == null)) return `<span class="cn-cell-gap cn-management-warning">${esc(__("Gestión: CxC con cobros por revisar"))} · N/D</span>`;
    const amount = rows.reduce((sum, row) => sum + Math.round(Number(row.amount_usd || 0) * 100), 0) / 100;
    return amount > MONEY_EPSILON ? `<span class="cn-cell-gap cn-management-warning">${esc(__("Gestión: CxC pendiente"))} ${money(amount)}</span>` : "";
}

function renderCashPanel(receipts) {
    const unique = [...new Map(receipts.map(deposit => [deposit.name, deposit])).values()];
    return `<section class="cn-cash-panel"><h4>${esc(__("Depósitos recibidos en el mes"))} · ${money(summarizeCash(unique).total_usd)}</h4>
        <p class="text-muted">${esc(__("Por fecha del depósito, aunque paguen períodos anteriores. No se suman nuevamente al importe asignado de los períodos."))}</p>
        ${unique.length ? `<div class="cn-period-cards cn-cash-cards">${unique.map(renderCashCard).join("")}</div>` : `<p>${esc(__("No hay depósitos confirmados recibidos en este mes."))}</p>`}
    </section>`;
}

function renderCashCreditBalances(deposit) {
    const creditBalances = [
        ["Saldo a favor del cliente", deposit.client_credit_usd],
        ["Saldo a favor de la empresa", deposit.company_credit_usd],
        ["Saldo a favor sin detalle", deposit.undetailed_credit_usd ?? (
            (Math.round(Number(deposit.credit_balance_usd || 0) * 100)
                - Math.round(Number(deposit.client_credit_usd || 0) * 100)
                - Math.round(Number(deposit.company_credit_usd || 0) * 100)) / 100
        )],
    ];
    return creditBalances.filter(([, amount]) => Number(amount) > MONEY_EPSILON)
        .map(([label, amount]) => `<span class="cn-cell-credit">${esc(__(label))}: ${money(amount)}</span>`).join("");
}

function renderCashCard(deposit) {
    const state = deposit.needs_review || !deposit.settled ? "diferencia" : "conciliado";
    return `<button type="button" class="cn-period-card cn-${state}" data-cash-deposit="${esc(deposit.name)}">
        <span class="cn-period-card-name">${esc(deposit.reference || deposit.name)}</span>
        <span class="cn-cell-sub">${esc(displayDate(deposit.date))} · ${esc(deposit.name)}</span>
        <span class="cn-cell-sub">${esc(__("Cuenta bancaria"))}: ${esc(deposit.bank_account || __("Sin cuenta asignada"))}</span>
        <strong class="cn-cell-amount">${money(deposit.total_usd)}</strong>
        <span class="cn-badge">${esc(cashStatus(deposit))}</span>
        <span class="cn-cell-sub">${esc(__("A créditos"))}: ${money(deposit.credits_usd)}</span>
        ${Math.abs(Number(deposit.other_usd)) > MONEY_EPSILON ? `<span class="cn-cell-sub">${esc(__("Otros conceptos"))}: ${signedMoney(deposit.other_usd)}</span>` : ""}
        ${renderCashCreditBalances(deposit)}
        ${renderCashReceivables([deposit])}
        ${Number(deposit.credit_management_pending_usd) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Pendiente de gestión"))}: ${money(deposit.credit_management_pending_usd)}</span>` : ""}
        ${Math.abs(Number(deposit.unclassified_usd)) > MONEY_EPSILON ? `<span class="cn-cell-gap">${esc(__("Sin identificar"))}: ${signedMoney(deposit.unclassified_usd)}</span>` : ""}
        ${deposit.shared ? `<span class="cn-cell-sub">${esc(__("Distribuido entre varios períodos"))}</span>` : ""}
        <span class="cn-period-card-open">${esc(__("Ver distribución del depósito"))} →</span>
    </button>`;
}

function showCashDeposit(deposit, parentDialog) {
    const dialog = new frappe.ui.Dialog({
        title: __("Distribución del depósito"), size: "extra-large", animate: false,
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
    const creditUndetailed = deposit.undetailed_credit_usd ?? deposit.credit_balance_usd;
    for (const [field, type, label] of [
        ["undetailed_credit_usd", __("Otros saldos a favor"), __("No aplicado a créditos")],
        ["unclassified_usd", __("Sin identificar"), __("Pendiente de asignar o justificar")],
        ["review_usd", __("Distribución por revisar"), __("El importe asignado no coincide con su desglose")],
    ]) {
        const amount = field === "undetailed_credit_usd" ? creditUndetailed : deposit[field];
        if (Math.abs(Number(amount || 0)) > MONEY_EPSILON) lines.push({type, label, amount_usd: amount});
    }
    const original = new Intl.NumberFormat("es-NI", {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(deposit.original_amount || 0));
    const summaryTotal = cashTableTotal(lines);
    return `<div class="cn-cash-distribution">
        <div class="cn-kpis cn-cash-kpis">
            <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Referencia del depósito"))}</div><div class="cn-kpi-value">${esc(deposit.reference || deposit.name)}</div><div class="cn-cash-kpi-note">${esc(displayDate(deposit.date))}<br>${esc(deposit.name)}</div></div>
            <div class="cn-kpi"><div class="cn-kpi-label">${esc(__("Cuenta bancaria"))}</div><div class="cn-kpi-value">${esc(deposit.bank_account || __("Sin cuenta asignada"))}</div>${deposit.currency ? `<div class="cn-cash-kpi-note">${esc(__("Original"))}: ${esc(deposit.currency)} ${original}</div>` : ""}</div>
            <div class="cn-kpi cn-kpi-remitted"><div class="cn-kpi-label">${esc(__("Depositado total USD"))}</div><div class="cn-kpi-value">${money(deposit.total_usd)}</div></div>
            <div class="cn-kpi cn-cash-result"><div class="cn-kpi-label">${esc(__("Estado de conciliación"))}</div><div class="cn-kpi-value">${esc(cashStatus(deposit))}</div>${cashStatus(deposit) !== deposit.result ? `<details class="cn-cash-kpi-note"><summary>${esc(__("Resultado guardado (auditoría)"))}</summary>${esc(deposit.result || __("Pendiente"))}</details>` : ""}</div>
        </div>
        ${renderCashReceivables([deposit])}
        <div class="cn-list-scroll"><table class="cn-detail-table cn-cash-distribution-summary"><caption>${esc(__("Resumen de distribución"))}</caption><thead><tr><th>${esc(__("Destino"))}</th><th>${esc(__("Período / concepto"))}</th><th>${esc(__("Mes de cobranza"))}</th><th>${esc(__("US$"))}</th></tr></thead><tbody>
            ${lines.map(item => `<tr><td>${esc(item.type)}</td><td>${esc(item.label)}${renderCreditManagementBadge(item)}${renderCashCreditIdentity(item)}${item.employer ? `<div>${esc(__("Empresa"))}: ${esc(item.employer)}</div>` : ""}</td><td>${esc(item.month || "—")}</td><td class="cn-number">${signedMoney(item.amount_usd)}</td></tr>`).join("")}
        </tbody><tfoot><tr><th colspan="3">${esc(__("Total resumen"))}</th><th class="cn-number">${money(summaryTotal)}</th></tr></tfoot></table></div>
        ${lines.map(renderCreditPeople).join("")}
        ${Number(deposit.credit_balance_usd) > MONEY_EPSILON ? `<p class="cn-cell-credit">${esc(__("El saldo a favor requiere seguimiento. Su documentación no significa que ya fue reembolsado."))}</p>` : ""}
        ${Number(deposit.client_credit_usd) > MONEY_EPSILON ? `<p>${esc(__("Saldo de clientes pendiente de gestión"))}: ${money(deposit.client_credit_pending_usd)}</p>` : ""}
        ${Number(deposit.company_credit_usd) > MONEY_EPSILON ? `<p>${esc(__("Saldo de empresa pendiente de gestión"))}: ${money(deposit.company_credit_pending_usd)}</p>` : ""}
    </div>`;
}

function renderCashCreditIdentity(destination) {
    if (destination.type !== "Saldo a favor del cliente") return "";
    const people = destination.people?.length ? destination.people : [{}];
    return people.map(person => `<div><strong>${esc(__("Cliente"))}: ${esc(person.client_name || __("No disponible"))}</strong></div>
        <div>${esc(__("Nro. Cliente"))}: ${esc(person.client_number || __("No disponible"))}</div>`).join("");
}

function renderCreditManagementBadge(destination) {
    if (!["Saldo a favor del cliente", "Saldo a favor de la empresa"].includes(destination.type)) return "";
    const status = destination.management_status || "Pendiente";
    const stateClass = ({"Pendiente": "pending", "Parcialmente resuelto": "partial", "Resuelto": "resolved"})[status] || "other";
    return `<span class="cn-badge cn-credit-management-${stateClass}">${esc(status)}</span>`;
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

function renderOperationalSummary(period) {
    const pending = period.pending_application_usd;
    const unlinked = period.unlinked_application_rows || [];
    const unlinkedAmount = unlinked.reduce((cents, row) => cents + Math.round(Number(row.net_applied_usd ?? row.amount ?? 0) * 100), 0) / 100;
    const cards = [
        ["Cobranza enviada", period.expected_usd, "Importe solicitado a la empresa"],
        ["Aplicado en el core", periodAmounts(period).applied_usd, "Pagos registrados en los créditos antes de compensaciones"],
        ["Asignado", periodAmounts(period).remitted_usd, "Cobertura con depósitos y partidas complementarias confirmadas"],
        ["Pago recibido por aplicar", pending, "Identificado en el depósito; falta aplicación"],
    ];
    const controls = [
        ["Diferencia cobranza/aplicación (informativo)", period.worker_gap_usd],
        ["Saldo a favor documentado", period.documented_credit_usd],
        ["Depósito sin asignar", period.unclassified_deposit_usd],
    ];
    return `<div class="cn-kpis cn-period-summary">${cards.map(([label, amount, note], index) => `<div class="cn-kpi ${index === 3 && Number(amount) > 0 ? "cn-kpi-gap" : ""}">
        <div class="cn-kpi-label">${esc(__(label))}</div><div class="cn-kpi-value">${amount == null ? "—" : money(amount)}</div><p>${esc(__(note))}</p>
        ${index === 1 && unlinked.length ? `<p>${unlinked.length} ${esc(__(unlinked.length === 1 ? "aplicación sin cobranza vinculada" : "aplicaciones sin cobranza vinculada"))}: ${money(unlinkedAmount)}</p>` : ""}</div>`).join("")}</div>
        ${period.payment_evidence_restricted ? `<p class="cn-period-notice">${esc(__("Vista parcial: faltan permisos para consultar parte de la evidencia de depósito."))}</p>` : ""}
        ${Number(pending) > 0 ? `<p class="cn-period-notice">${esc(__("Hay pagos recibidos pendientes de aplicar en el core. El importe ya está en el depósito; no es saldo a favor ni dinero adicional."))}</p>` : ""}
        <details class="cn-period-secondary"><summary>${esc(__("Ver control de cobranza"))}</summary>
            ${renderMissingQualityBasis(period)}
            <dl class="cn-period-controls">${controls.map(([label, value]) => `<div><dt>${esc(__(label))}</dt><dd>${money(value)}</dd></div>`).join("")}</dl>
            <p>${esc(__("Estos controles describen etapas diferentes y no se suman. El depósito sin asignar puede incluir el pago recibido por aplicar."))}</p>
        </details>`;
}

function renderPeriodExceptions(exceptions) {
    if (!(exceptions || []).length) return `<div class="cn-empty">${esc(__("No hay excepciones abiertas."))}</div>`;
    return `<div class="cn-list-scroll"><table class="cn-detail-table cn-exception-table"><thead><tr>
        <th>${esc(__("Excepción"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Causa y motivo"))}</th>
        <th>${esc(__("Importe"))}</th><th>${esc(__("Estado"))}</th><th>${esc(__("Próxima acción"))}</th><th>${esc(__("Compromiso"))}</th>
    </tr></thead><tbody>${exceptions.map((item) => `<tr>
        <td><button class="cn-text-link" type="button" data-exception="${esc(item.name)}">${esc(item.name)}</button></td>
        <td><strong>${esc(item.client_name || "—")}</strong>
            <div class="cn-period-person">${esc(__("Nro. Cliente"))}: ${esc(item.client_number || "—")}<br>${esc(__("Crédito"))}: ${esc(item.loan_number || "—")}</div></td>
        <td>${esc(item.cause_category || item.exception_type)}<br>${esc(item.description)}</td>
        <td class="cn-number">${money(item.amount_usd)}</td><td>${esc(item.status)}</td>
        <td>${esc(item.next_action || "—")}</td><td>${esc(displayDate(item.commitment_date) || "—")}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function hasApplicationDepositDifference(row) {
    return Number(row.historical_balance_usd || 0) > MONEY_EPSILON
        || ["Diferencia de importe", "Falta tipo de cambio", "Ambiguo"].includes(row.deposit_match_status);
}

function renderPeriodApplicationTable(period, canCreateException) {
    const rows = period.historical_rows || [];
    if (!rows.length) return `<div class="cn-empty">${esc(__("No hay aplicaciones asignadas al período."))}</div>`;
    const historical = period.reconciliation_mode === "Historica";
    const unlinked = new Set((period.unlinked_application_rows || []).map(row => row.name));
    const differenceCount = rows.filter(hasApplicationDepositDifference).length;
    return `<div class="cn-period-difference-filter"><label><input type="checkbox" data-application-difference-filter> ${esc(__("Mostrar solo diferencias entre aplicación y depósito"))}</label>
            <span>${differenceCount} ${esc(__(differenceCount === 1 ? "aplicación con diferencia" : "aplicaciones con diferencia"))}</span></div>
        <div class="cn-list-scroll" data-period-application-region tabindex="0" role="region" aria-label="${esc(__("Aplicaciones contra depósitos"))}"><table class="cn-detail-table cn-period-applications"><thead><tr>
            <th>${esc(__("Fecha"))}</th><th>${esc(__("Cliente / crédito"))}</th><th>${esc(__("Aplicado neto US$"))}</th>
            <th>${esc(__("Depósito asignado US$"))}</th><th>${esc(__("Pendiente US$"))}</th><th>${esc(__("Depósitos"))}</th>
            <th>${esc(__(historical ? "Excepción" : "Cobranza"))}</th>
        </tr></thead><tbody>${rows.map(row => `<tr data-application-difference="${hasApplicationDepositDifference(row) ? "1" : "0"}">
            <td>${esc(displayDate(row.event_date))}</td>
            <td>${esc(row.client_number || "—")} · ${esc(row.client_name || "—")} / ${esc(row.loan_number || "—")}
                <br><button type="button" class="cn-text-link" data-application-import="${esc(row.parent)}">${esc(__("Referencia"))}: ${esc(row.reference || row.voucher || row.parent)}</button></td>
            <td class="cn-number">${money(row.net_applied_usd ?? row.amount)}</td>
            <td class="cn-number">${money(row.historical_remitted_usd)}</td>
            <td class="cn-number">${money(row.historical_balance_usd)}${row.deposit_match_status ? `<div class="text-muted">${esc(__(row.deposit_match_status))}</div>` : ""}</td>
            <td>${allocationLines(row.historical_detail)}</td>
            <td>${historical ? applicationExceptionAction(period, row, canCreateException) : esc(__(unlinked.has(row.name) ? "Sin cobranza vinculada" : "Cobranza vinculada"))}</td>
        </tr>`).join("")}</tbody></table></div>
        <div class="cn-empty" data-application-difference-empty hidden>${esc(__("No hay diferencias entre aplicaciones y depósitos en este período."))}</div>`;
}

function applyTableDifferenceFilter(detail, filtered, regionSelector, rowSelector, differenceKey, emptySelector) {
    const region = detail.querySelector(regionSelector);
    const rows = [...(region?.querySelectorAll(rowSelector) || [])];
    let visible = 0;
    rows.forEach(row => {
        const show = !filtered || row.dataset[differenceKey] === "1";
        row.hidden = !show;
        if (show) visible++;
    });
    if (region) region.hidden = filtered && visible === 0;
    const empty = detail.querySelector(emptySelector);
    if (empty) empty.hidden = !filtered || visible > 0;
    return visible;
}

function applyPeriodDifferenceFilter(detail, filtered) {
    return applyTableDifferenceFilter(detail, filtered, "[data-period-collection-region]",
        "tbody tr[data-quality-difference]", "qualityDifference", "[data-period-difference-empty]");
}

function applyApplicationDifferenceFilter(detail, filtered) {
    return applyTableDifferenceFilter(detail, filtered, "[data-period-application-region]",
        "tbody tr[data-application-difference]", "applicationDifference", "[data-application-difference-empty]");
}

function renderPeriodCollectionTable(period) {
    if (!(period.rows || []).length) return `<div class="cn-empty">${esc(__("Sin detalle de cobranza."))}</div>`;
    const differenceCount = period.rows.filter(row => row.quality_difference_usd != null && Number(row.quality_difference_usd) !== 0).length;
    return `<p class="cn-period-table-note">${esc(__("Importes en US$. Depósito asignado y pago recibido por aplicar se muestran por separado. Abra Ver detalle para consultar depósitos, ajustes y antecedentes."))}</p>
        <div class="cn-period-difference-filter"><label><input type="checkbox" data-period-difference-filter> ${esc(__("Mostrar solo diferencias de cobranza/aplicación"))}</label>
            <span>${differenceCount} ${esc(__(differenceCount === 1 ? "fila con diferencia" : "filas con diferencia"))}</span></div>
        <p class="cn-period-scroll-hint">${esc(__("Desplace la tabla hacia la derecha para ver todas las columnas."))} →</p>
        <div class="cn-list-scroll" data-period-collection-region tabindex="0" role="region" aria-label="${esc(__("Cobranza y aplicación por cliente"))}"><table class="cn-detail-table cn-period-collections"><thead><tr>
        ${["Cliente / crédito", "Cobranza US$", "Aplicado US$", "Diferencia US$", "Depósito asignado", "Pago por aplicar", "Resultado del control / detalle"].map(label => `<th scope="col">${esc(__(label))}</th>`).join("")}
        </tr></thead><tbody>${period.rows.map(row => {
            const pending = row.pending_payment_usd;
            const note = row.inherited_exception_comment || row.first_exception_comment || row.application_comment;
            const difference = row.quality_difference_usd;
            const hasDifference = difference != null && Number(difference) !== 0;
            const status = row.quality_status || (difference == null ? __("Revisar conversión") : Number(difference) === 0 ? __("Conforme según cobranza") : __("Con diferencias"));
            return `<tr data-quality-difference="${hasDifference ? "1" : "0"}" class="${Number(pending) > 0 ? "cn-payment-pending" : ""}">
                <td><strong>${esc(row.client_name || "—")}</strong><div class="cn-period-person">${esc(__("Cliente"))}: ${esc(row.client_number || "—")}<br>${esc(__("Crédito"))}: <span class="cn-nowrap">${esc(row.loan_number || "—")}</span> · ${esc(__("Cuota"))}: ${esc(row.installment_number || "—")}</div></td>
                ${[row.quality_status === "Revisar base de comparación" ? null : row.expected_usd, row.applied_usd, difference, row.remitted_usd].map(value => `<td class="cn-number">${value == null ? "—" : money(value)}</td>`).join("")}
                <td class="cn-number">${pending == null ? "—" : money(pending)}</td>
                <td><span class="cn-period-row-status">${esc(status)}</span><details class="cn-period-row-detail"><summary>${esc(__("Ver detalle"))}</summary>
                    <div><strong>${esc(__("Depósitos vinculados"))}</strong><div>${allocationLines(row.remittance_detail)}</div></div>
                    ${(row.pending_payment_deposits || []).map(deposit => `<div><strong>${esc(__("Pago por aplicar"))}:</strong> <a href="/app/cn-remittance-allocation/${encodeURIComponent(deposit.name)}">${esc(deposit.name)}</a> <span class="cn-nowrap">${money(deposit.amount_usd)}</span></div>`).join("")}
                    <div>${esc(__("Partidas y ajustes"))}: <span class="cn-nowrap">${money(Number(row.complementary_usd || 0) + Number(row.rounding_adjustment_usd || 0))}</span></div>
                    <div>${esc(__("Control financiero"))}: ${esc(applicationStatusLabel(row.application_status))}</div>
                    ${note && String(note).trim() !== "0" ? `<div>${esc(__("Antecedente"))}: ${esc(note)}</div>` : ""}
                </details></td>
            </tr>`;
        }).join("")}</tbody></table></div>
        <div class="cn-empty" data-period-difference-empty hidden>${esc(__("No hay diferencias de cobranza contra aplicación en este período."))}</div>`;
}

function renderWorkClient(item) {
    const exception = ["open_exception", "overdue_exception"].includes(item.kind);
    if (!exception && item.category !== "Saldo a favor del cliente" && item.kind !== "pending_application") return "";
    if (exception && ![item.client_name, item.client_number, item.loan_number].some(Boolean)) return "";
    const identifiers = [
        `${esc(__("Nro. Cliente"))}: ${esc(item.client_number || __("No informado"))}`,
        exception && item.loan_number ? `${esc(__("Crédito"))}: ${esc(item.loan_number)}` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="cn-work-client"><strong>${esc(item.client_name || __("Nombre no informado"))}</strong>
        <div>${identifiers}</div></div>`;
}

function renderWorkException(item) {
    if (!["open_exception", "overdue_exception"].includes(item.kind)) return "";
    const cause = item.cause_category && item.cause_category !== "Por determinar"
        ? `<div><strong>${esc(__("Causa"))}:</strong> ${esc(item.cause_category)}</div>` : "";
    const description = String(item.description || "").trim();
    const reason = description ? `<div><strong>${esc(__("Motivo"))}:</strong> ${esc(description)}</div>` : "";
    return cause || reason ? `<div class="cn-work-exception">${cause}${reason}</div>` : "";
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

function operativeCycleLabel(period) {
    return period.collection_cycle === "Fecha exacta"
        ? `${__("Fecha exacta")} · ${displayDate(period.cutoff_date)}`
        : period.collection_cycle || __("Mensual");
}

function cycleOrder(cycle) {
    return ({ "Primera quincena": 1, "Segunda quincena": 2, "Mensual": 3 })[cycle] || 4;
}

function historicalSortDate(period) {
    return period.historical_application_date || period.historical_start_date || period.payroll_month || "";
}

function periodCardDate(period) {
    const date = period.reconciliation_mode === "Historica" ? historicalSortDate(period)
        : period.cutoff_date || period.payroll_month || (period.month && `${period.month}-01`);
    return String(date || "9999-12-31").slice(0, 10);
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

function createCalendarFullscreen(root, wrapper, onChange) {
    let active = false, placeholder = null, nativeOwned = false, generation = 0;
    let savedFocus = null;
    const hasModal = () => Array.from(document.querySelectorAll(".modal.show, .modal.in, .modal-backdrop"))
        .some(element => element.getClientRects().length > 0);
    const focusButton = () => root.querySelector("[data-calendar-fullscreen]")?.focus({preventScroll: true});
    const onHide = () => exit(true, false);
    function onFullscreenChange() {
        if (active && nativeOwned && !document.fullscreenElement) exit(false);
    }
    function onKeydown(event) {
        // Let Frappe own Escape and focus while ANY modal is open.
        if (!active || hasModal()) return;
        if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            exit();
        } else if (event.key === "Tab") {
            const controls = Array.from(root.querySelectorAll('button, input, select, a[href], [tabindex="0"]'))
                .filter(element => !element.disabled && element.getClientRects().length > 0);
            const first = controls[0], last = controls[controls.length - 1];
            if (!first) return;
            if (!root.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)
                || (event.shiftKey && document.activeElement === first)) {
                event.preventDefault();
                (event.shiftKey ? last : first).focus();
            }
        }
    }
    async function enter() {
        if (active) return;
        const attempt = ++generation;
        active = true;
        savedFocus = document.activeElement;
        placeholder = document.createComment("calendar-fullscreen-home");
        root.before(placeholder);
        // Portal the same root, preserving delegated events and table scroll.
        // This avoids ancestor stacking contexts while keeping Frappe modals above it.
        document.body.appendChild(root);
        root.classList.add("cn-calendar-expanded");
        document.body.classList.add("cn-calendar-expanded-body");
        document.addEventListener("keydown", onKeydown, true);
        document.addEventListener("fullscreenchange", onFullscreenChange);
        $(wrapper).on("hide.cnCalendarFullscreen", onHide);
        onChange(true);
        focusButton();
        // Fullscreen the DOCUMENT, not the calendar: dialogs/backdrops are body
        // siblings and would otherwise be excluded from the browser's top layer.
        if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
            try {
                await document.documentElement.requestFullscreen();
                if (attempt === generation && active) nativeOwned = true;
                else if (!active && document.fullscreenElement === document.documentElement) {
                    await document.exitFullscreen();
                } else if (active) nativeOwned = true;
            } catch (_error) { /* Keep the viewport-sized fallback usable. */ }
        }
    }
    async function exit(exitNative = true, restoreFocus = true) {
        if (!active) return;
        generation++;
        active = false;
        root.classList.remove("cn-calendar-expanded");
        document.body.classList.remove("cn-calendar-expanded-body");
        placeholder?.replaceWith(root);
        placeholder = null;
        document.removeEventListener("keydown", onKeydown, true);
        document.removeEventListener("fullscreenchange", onFullscreenChange);
        $(wrapper).off("hide.cnCalendarFullscreen", onHide);
        onChange(false);
        if (restoreFocus) {
            if (savedFocus?.isConnected) savedFocus.focus({preventScroll: true});
            else focusButton();
        }
        const leaveNative = nativeOwned && document.fullscreenElement === document.documentElement;
        nativeOwned = false;
        if (exitNative && leaveNative) {
            try { await document.exitFullscreen(); } catch (_error) { /* Browser Escape remains available. */ }
        }
    }
    return {enter, exit, toggle: () => active ? exit() : enter(), get active() {return active;}};
}

function renderControlKpis(data, failed = false) {
    if (!data) return `<div class="cn-kpi-message" role="status">${esc(__(failed
        ? "No se pudieron cargar las cifras. No se muestran importes en cero para sustituir el error."
        : "Calculando cifras de control..."))}${failed ? ` <button type="button" class="btn btn-default btn-sm" data-kpis-retry>${esc(__("Reintentar"))}</button>` : ""}</div>`;
    const a = data.applications, d = data.deposits, c = data.credits, e = data.exceptions, r = data.receivables;
    const knownMoney = value => value == null ? "N/D" : money(value);
    const card = (label, value, note, kind, available, action = "", actionLabel = "") => `
        <div class="cn-kpi cn-kpi-${kind}"><div class="cn-kpi-label">${esc(__(label))}</div>
            <div class="cn-kpi-value">${available ? esc(value) : "—"}</div>
            <div class="cn-kpi-note">${available ? esc(note) : esc(__("No disponible con sus permisos."))}</div>
            ${available && action ? `<button type="button" class="cn-text-link" data-kpi-action="${action}">${esc(__(actionLabel))}</button>` : ""}
        </div>`;
    const notes = [];
    if (a?.missing_fx_count) notes.push(`${a.missing_fx_count} ${__("aplicaciones sin conversión US$. Total pendiente de determinar; complete el tipo de cambio.")}`);
    if (a?.review_count) notes.push(`${a.review_count} ${__("aplicaciones con vínculos inconsistentes. Revise su distribución para determinar el saldo.")}`);
    if (a?.pending_usd == null && a) notes.push(`${__("Subtotal conocido pendiente US$")}: ${money(a.known_pending_usd)}.`);
    if (r?.review_count) notes.push(`${r.review_count} ${__("CxC por ajustes con cobros o compensaciones inconsistentes. Su total queda sin determinar.")}`);
    if (a?.without_date_usd > MONEY_EPSILON) notes.push(`${__("Pendiente sin vencimiento determinado")}: ${money(a.without_date_usd)}. ${__("No se incluye en el vencido.")}`);
    if (d?.overallocated_count) notes.push(`${d.overallocated_count} ${__("depósitos con distribución superior al importe recibido; revisar. No compensan los saldos sin asignar de otros depósitos.")}`);
    return `<div class="cn-kpis cn-control-kpis">
        ${card("Aplicado neto US$", knownMoney(a?.net_applied_usd), `${__("Sin período")}: ${money(a?.unlinked_usd)}. ${__("Incluye ajustes confirmados de aplicación.")}`, "applied", a)}
        ${card("Depósitos recibidos US$", money(d?.received_usd), `${d?.deposit_count || 0} ${__("depósitos confirmados, por su importe completo.")}`, "remitted", d)}
        ${card("Aplicado pendiente de conciliar US$", knownMoney(a?.pending_usd), `${__("Vencido")}: ${knownMoney(a?.overdue_usd)}. ${__("Solo descuenta distribuciones vinculadas.")}`, "gap", a, "aging", "Ver antigüedad")}
        ${card("CxC por ajustes US$", knownMoney(r?.pending_usd), `${__("Vencido")}: ${knownMoney(r?.overdue_usd)} · ${__("Sin fecha compromiso")}: ${knownMoney(r?.without_date_usd)}. ${__("Pendiente de cobro o compensación vinculada.")}`, "gap", r, "receivable-aging", "Ver antigüedad de ajustes")}
        ${card("Depósitos sin asignar US$", money(d?.unassigned_usd), `${d?.unassigned_count || 0} ${__("depósitos con dinero sin destino identificado.")}`, "gap", d)}
        ${card("Saldos a favor por gestionar US$", money(c?.credit_pending_usd), `${__("Clientes pendientes")}: ${money(c?.client_pending_usd)} · ${__("Empresas pendientes")}: ${money(c?.company_documented_usd)}. ${__("Importes documentados que aún requieren devolución o aplicación futura.")}`, "surplus", c)}
        ${card("Excepciones vencidas", String(e?.count || 0), __("Abiertas o en revisión, con fecha compromiso vencida. Incluye casos sin período."), "gap", e,
            e?.count && e.filters ? "exceptions" : "", "Ver excepciones")}
    </div>
    ${notes.length ? `<div class="cn-kpi-warning" role="status">${notes.map(note => `<p>${esc(note)}</p>`).join("")}</div>` : ""}
    <p class="cn-kpi-scope">${esc(__("Saldo actual, no reconstrucción histórica. Año: aplicaciones por mes de cobranza (sin período, fecha de aplicación); depósitos por fecha de depósito; saldos a favor por fecha de partida; excepciones por fecha compromiso. El año del calendario no cambia estas cifras. No reste los depósitos recibidos del aplicado para calcular el pendiente."))}</p>`;
}

function styles() {
    return `<style>
        .cn-control, .modal {
            --cn-surface: var(--bg-color, #fff);
            --cn-muted-surface: color-mix(in srgb, var(--control-bg, #f8fafc) 40%, var(--cn-surface));
            --cn-border: var(--border-color, #e2e8f0);
            --cn-text: color-mix(in srgb, var(--text-color, #334155) 82%, var(--cn-surface));
            --cn-muted: color-mix(in srgb, var(--text-muted, #64748b) 85%, var(--cn-surface));
            --cn-primary: var(--primary, #2563eb);
            --cn-success: color-mix(in srgb, #15803d 65%, var(--cn-text));
            --cn-success-bg: color-mix(in srgb, #16a34a 7%, var(--cn-surface));
            --cn-info: color-mix(in srgb, #1d4ed8 65%, var(--cn-text));
            --cn-info-bg: color-mix(in srgb, #3b82f6 7%, var(--cn-surface));
            --cn-warning: color-mix(in srgb, #b45309 65%, var(--cn-text));
            --cn-warning-bg: color-mix(in srgb, #f59e0b 7%, var(--cn-surface));
            --cn-orange: color-mix(in srgb, #c2410c 65%, var(--cn-text));
            --cn-orange-bg: color-mix(in srgb, #f97316 7%, var(--cn-surface));
            --cn-danger: color-mix(in srgb, #b91c1c 65%, var(--cn-text));
            --cn-danger-bg: color-mix(in srgb, #ef4444 7%, var(--cn-surface));
            --cn-purple: color-mix(in srgb, #7c3aed 65%, var(--cn-text));
            --cn-purple-bg: color-mix(in srgb, #8b5cf6 7%, var(--cn-surface));
        }
        .cn-control { padding: 12px 4px 32px; color: var(--cn-text); }
        .cn-intro { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin: 6px 0 18px; }
        .cn-intro-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .cn-intro h2 { font-size: 21px; font-weight: 700; margin: 0 0 4px; color: var(--heading-color, var(--cn-text)); }
        .cn-intro p, .cn-footnote { font-size: 12px; color: var(--cn-muted); margin: 0; }
        .cn-view-tabs { display: flex; flex-wrap: wrap; gap: 8px; border-bottom: 1px solid var(--border-color, #e2e8f0); margin-bottom: 16px; }
        .cn-view-tab { display: inline-flex; align-items: center; gap: 8px; border: 0; border-bottom: 3px solid transparent; background: transparent; padding: 12px 16px; font-size: 14px; font-weight: 600; color: var(--text-muted, #64748b); cursor: pointer; }
        .cn-view-tab:hover { background: var(--control-bg, #f1f5f9); }
        .cn-view-tab[aria-selected="true"] { color: var(--primary, #2563eb); border-bottom-color: currentColor; }
        .cn-view-tab:focus-visible, .cn-tab-panel:focus-visible { outline: 2px solid var(--primary, #2563eb); outline-offset: 2px; }
        .cn-tab-count { background: var(--control-bg, #f1f5f9); color: var(--text-color, #334155); border-radius: 12px; padding: 2px 8px; font-size: 13px; }
        .cn-tab-overdue { color: var(--cn-danger); font-size: 13px; }
        .cn-control .cn-tab-panel[hidden] { display: none !important; }
        body.cn-calendar-expanded-body { overflow: hidden !important; }
        .cn-control.cn-calendar-expanded { position: fixed; inset: 0; z-index: 1035; background: var(--bg-color, #f8fafc); margin: 0; padding: 12px; width: 100%; height: 100vh; height: 100dvh; box-sizing: border-box; overflow: hidden; }
        .cn-calendar-expanded > :not(style):not([data-control-panel="calendar"]) { display: none !important; }
        .cn-calendar-expanded [data-control-panel="calendar"] { height: 100%; }
        .cn-calendar-expanded [data-calendar-section] { height: 100%; display: flex; flex-direction: column; margin: 0; min-height: 0; }
        .cn-calendar-expanded .cn-panel-head { flex-shrink: 0; }
        .cn-calendar-expanded .cn-matrix-scroll { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; }
        .cn-calendar-expanded [data-calendar-section] > p { padding: 8px 16px; margin: 0; }
        .cn-fullscreen-button { flex-shrink: 0; }
        .cn-year { background: var(--cn-info-bg); border-radius: 8px; padding: 6px 12px; color: var(--cn-info); font-weight: 700; }
        .cn-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 18px; }
        .cn-kpi { min-width: 0; padding: 13px; border: 1px solid var(--cn-border); border-radius: 10px; background: var(--cn-surface); color: var(--cn-text); box-shadow: 0 2px 6px #0f172a0b; }
        .cn-kpi-label { text-transform: uppercase; letter-spacing: .04em; color: var(--cn-muted); font-size: 10px; font-weight: 700; }
        .cn-kpi-value { font-size: 19px; font-weight: 700; color: var(--cn-text); margin-top: 5px; white-space: nowrap; }
        .cn-kpi-remitted .cn-kpi-value { color: var(--cn-success); } .cn-kpi-gap .cn-kpi-value { color: var(--cn-warning); }
        .cn-kpi-surplus .cn-kpi-value { color: var(--cn-purple); }
        .cn-control-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
        [data-kpi-content] { padding: 14px; }
        .cn-control-kpis .cn-kpi-label { font-size: 13px; text-transform: none; letter-spacing: normal; }
        .cn-control-kpis .cn-kpi-value { font-size: 23px; white-space: normal; overflow-wrap: anywhere; }
        .cn-control-kpis .cn-text-link { margin-top: 10px; }
        .cn-kpi-note, .cn-kpi-scope, .cn-kpi-message, .cn-process-figures { font-size: 13px; line-height: 1.5; }
        .cn-kpi-note { color: var(--cn-muted); margin-top: 8px; }
        .cn-kpi-scope { color: var(--cn-muted); }
        .cn-kpi-message { padding: 12px 0; }
        .cn-kpi-warning { background: var(--cn-warning-bg); border-left: 3px solid var(--cn-warning); color: var(--cn-text); padding: 8px 12px; margin-bottom: 12px; }
        .cn-kpi-warning p { margin: 4px 0; }
        .cn-process-figures { border-top: 1px solid var(--cn-border); padding: 10px 14px 14px; }
        .cn-process-figures dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; }
        .cn-process-figures dl > div { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--cn-border); padding: 6px 0; }
        .cn-process-figures dt { font-weight: 400; } .cn-process-figures dd { margin: 0; white-space: nowrap; font-weight: 600; }
        @media(max-width: 900px) { .cn-control-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media(max-width: 650px) { .cn-control-kpis, .cn-process-figures dl { grid-template-columns: 1fr; } }
        .cn-inherited-note { color: var(--cn-warning); font-size: 11px; }
        .cn-panel { background: var(--cn-surface); color: var(--cn-text); border: 1px solid var(--cn-border); border-radius: 12px; overflow: hidden; margin-bottom: 18px; box-shadow: 0 2px 8px #0f172a0a; }
        .cn-panel-head { display: flex; flex-wrap: wrap; gap: 10px; justify-content: space-between; align-items: center; padding: 13px 16px; border-bottom: 1px solid var(--cn-border); }
        .cn-panel-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: var(--heading-color, var(--cn-text)); }
        .cn-panel-head span { font-size: 11px; color: var(--cn-muted); }
        .cn-summary-toggle { display: flex; align-items: center; gap: 6px; margin: 0 12px 0 auto; font-size: 12px; cursor: pointer; }
        .cn-calendar-filter { white-space: nowrap; }
        .cn-calendar-filter select { width: 100px; }
        .cn-period-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 12px; max-height: 60vh; overflow-y: auto; padding: 4px; }
        .cn-period-card { display: flex; flex-direction: column; align-items: flex-start; text-align: left; padding: 16px; border: 1px solid var(--cn-border); border-radius: 10px; background: var(--cn-surface); color: var(--cn-text); cursor: pointer; min-width: 0; overflow-wrap: anywhere; }
        .cn-period-card:hover { box-shadow: 0 3px 12px #0f172a18; }
        .cn-period-card:focus-visible { outline: 2px solid var(--cn-primary); outline-offset: 2px; }
        .cn-period-card-name { font-size: 14px; font-weight: 700; margin-bottom: 5px; }
        .cn-cash-summary { display: block; width: 100%; border-top: 1px solid var(--border-color, #cbd5e1); margin-top: 9px; padding-top: 7px; }
        .cn-cash-review { color: var(--cn-warning); }
        .cn-cash-panel { border-top: 1px solid var(--border-color, #e2e8f0); padding-top: 16px; margin-top: 16px; }
        .cn-cash-cards { max-height: 40vh; }
        .cn-cash-distribution .cn-badge { padding: 4px 8px; background: var(--control-bg, #f1f5f9); }
        .cn-cash-distribution .cn-credit-management-pending { background: var(--cn-warning-bg); color: var(--cn-warning); }
        .cn-cash-distribution .cn-credit-management-partial { background: var(--cn-info-bg); color: var(--cn-info); }
        .cn-cash-distribution .cn-credit-management-resolved { background: var(--cn-success-bg); color: var(--cn-success); }
        .cn-cash-distribution .cn-credit-management-other { background: var(--cn-muted-surface); color: var(--cn-text); }
        .cn-cash-distribution .cn-cash-kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .cn-cash-kpis .cn-kpi-value { white-space: normal; overflow-wrap: anywhere; }
        .cn-cash-kpi-note { margin-top: 6px; color: var(--text-muted, #64748b); font-size: 12px; overflow-wrap: anywhere; }
        .cn-cash-distribution .cn-list-scroll { margin-bottom: 18px; }
        .cn-cash-distribution caption { caption-side: top; padding: 10px 0; font-weight: 600; color: var(--cn-text); }
        .cn-cash-distribution tfoot th { border-top: 2px solid var(--cn-border); }
        @media(max-width: 900px) { .cn-cash-distribution .cn-cash-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media(max-width: 650px) { .cn-cash-distribution .cn-cash-kpis { grid-template-columns: 1fr; } }
        .cn-period-remark { display: block; width: 100%; margin: 10px 0; padding: 8px 10px; border-left: 2px solid var(--gray-400, #94a3b8); background: var(--cn-muted-surface); color: var(--cn-text); border-radius: 4px; font-size: 12px; overflow-wrap: anywhere; }
        .cn-period-remark strong { display: block; margin-bottom: 4px; }
        .cn-period-remark > span { display: block; white-space: pre-wrap; }
        .cn-period-card-amounts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; width: 100%; margin: 14px 0; font-size: 11px; }
        .cn-period-card-amounts strong { display: block; font-size: 15px; margin-top: 3px; }
        .cn-period-card-open { display: block; margin-top: auto; padding-top: 14px; font-size: 12px; font-weight: 600; color: var(--cn-primary); }
        .cn-work-panel { border-color: color-mix(in srgb, #3b82f6 40%, var(--cn-border)); }
        .cn-work-toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
        .cn-work-panel .cn-list-scroll { max-height: 60vh; overflow: auto; }
        .cn-work-table thead th { position: sticky; top: 0; z-index: 1; }
        .cn-work-table td { vertical-align: top; }
        .cn-work-table td:nth-child(3), .cn-work-table td:nth-child(4), .cn-work-table td:nth-child(6) { white-space: normal; min-width: 160px; }
        .cn-work-table td:nth-child(6) { min-width: 220px; }
        .cn-work-priority { display: inline-block; border-radius: 12px; padding: 3px 7px; font-weight: 700; background: var(--cn-muted-surface); color: var(--cn-text); }
        .cn-work-priority-0 { background: var(--cn-danger-bg); color: var(--cn-danger); }
        .cn-work-priority-1 { background: var(--cn-warning-bg); color: var(--cn-warning); }
        .cn-work-priority-2 { background: var(--cn-info-bg); color: var(--cn-info); }
        .cn-work-context, .cn-cut-note { color: var(--cn-muted); font-size: 10px; }
        .cn-work-exception { margin-top: 6px; color: var(--cn-muted); font-size: 12px; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere; }
        .cn-cut-banner { border-left: 3px solid var(--cn-info); background: var(--cn-info-bg); padding: 9px 12px; color: var(--cn-text); font-size: 11px; }
        .cn-work-overdue { color: var(--cn-danger); font-size: 10px; font-weight: 700; }
        .cn-work-more { display: flex; justify-content: center; align-items: center; gap: 12px; padding: 12px; color: var(--cn-muted); font-size: 11px; }
        .cn-collapsible summary { cursor: pointer; padding: 13px 16px; color: var(--heading-color, var(--cn-text)); font-size: 14px; font-weight: 700; }
        .cn-collapsible[open] summary { border-bottom: 1px solid var(--cn-border); }
        .cn-matrix-scroll, .cn-list-scroll { overflow-x: auto; }
        .cn-matrix { width: 100%; min-width: 2480px; border-collapse: separate; border-spacing: 0; table-layout: fixed; }
        .cn-matrix th { background: var(--cn-muted-surface); color: var(--cn-text); padding: 10px 6px; font-size: 12px; text-transform: uppercase; text-align: center; }
        .cn-matrix th.cn-company { width: 200px; text-align: left; position: sticky; left: 0; z-index: 2; }
        .cn-matrix tbody th.cn-company { background: var(--cn-surface); color: var(--cn-text); border-right: 2px solid var(--cn-border); border-bottom: 1px solid var(--cn-border); text-transform: none; font-size: 12px; }
        .cn-matrix td { border-bottom: 1px solid var(--cn-border); border-right: 1px solid var(--cn-border); vertical-align: top; padding: 0; }
        .cn-cell-button { width: 100%; border: 0; background: transparent; color: var(--cn-text); text-align: left; padding: 9px 7px; min-height: 88px; overflow-wrap: anywhere; }
        .cn-cell-button + .cn-cell-button { border-top: 1px dashed var(--cn-border); }
        .cn-cell-cycle { display: block; font-size: 12px; font-weight: 700; color: var(--cn-muted); margin-bottom: 3px; }
        .cn-cell-summary { padding: 6px 7px; font-size: 12px; font-weight: 700; color: var(--cn-text); border-bottom: 1px solid var(--cn-border); }
        .cn-cell-button:hover { background: var(--cn-muted-surface); } .cn-cell-amount { display: block; font-weight: 700; font-size: 14px; line-height: 1.35; }
        .cn-cell-sub, .cn-cell-gap, .cn-cell-credit { display: block; font-size: 12px; margin-top: 4px; }
        .cn-cell-gap { color: var(--cn-warning); font-weight: 700; } .cn-cell-credit { color: var(--cn-purple); font-weight: 700; }
        .cn-badge { display: inline-block; margin-top: 5px; border: 1px solid var(--cn-border); border-radius: 10px; padding: 3px 6px; background: var(--cn-muted-surface); color: var(--cn-text); font-size: 12px; font-weight: 700; }
        .cn-management-warning { padding: 4px 6px; border-radius: 4px; background: var(--cn-warning-bg); color: var(--cn-warning); }
        .cn-work-action + .cn-work-action { border-top: 1px solid var(--cn-border); margin-top: 8px; padding-top: 8px; }
        .cn-conciliado, .cn-historico_conciliado { background: var(--cn-success-bg); border-left: 3px solid var(--cn-success) !important; }
        .cn-parcial, .cn-historico_parcial { background: var(--cn-info-bg); border-left: 3px solid var(--cn-info) !important; }
        .cn-diferencia, .cn-historico_excepcion { background: var(--cn-warning-bg); border-left: 3px solid var(--cn-warning) !important; }
        .cn-en_transito, .cn-historico_pendiente { background: var(--cn-orange-bg); border-left: 3px solid var(--cn-orange) !important; }
        .cn-excedente, .cn-historico_excedente { background: var(--cn-purple-bg); border-left: 3px solid var(--cn-purple) !important; }
        .cn-pendiente_detalle { background: var(--cn-muted-surface); border-left: 3px solid var(--cn-muted) !important; }
        .cn-empty-cell { text-align: center; padding: 28px 4px !important; color: var(--cn-muted); }
        .cn-empty, .cn-loading { padding: 24px; text-align: center; color: var(--cn-muted); font-size: 12px; }
        .cn-detail-table { width: 100%; border-collapse: collapse; font-size: 13px; color: var(--cn-text); background: var(--cn-surface); }
        .cn-detail-table th { text-align: left; background: var(--cn-muted-surface); color: var(--cn-muted); text-transform: uppercase; font-size: 12px; letter-spacing: .04em; }
        .cn-detail-table th, .cn-detail-table td { padding: 9px 10px; border-bottom: 1px solid var(--cn-border); white-space: nowrap; }
        .cn-detail-table td:nth-child(3) { white-space: normal; min-width: 100px; }
        .cn-period-detail .cn-exception-table { min-width: 920px; }
        .cn-period-detail .cn-exception-table th:nth-child(2) { min-width: 210px; }
        .cn-period-detail .cn-exception-table th:nth-child(3) { min-width: 180px; }
        .cn-period-detail .cn-exception-table th:nth-child(6) { min-width: 170px; }
        .cn-detail-table .cn-number { text-align: right; font-weight: 600; }
        .cn-period-detail .cn-detail-table { font-size: 14px; line-height: 1.45; }
        .cn-period-detail .cn-detail-table th { font-size: 12px; letter-spacing: normal; text-transform: none; }
        .cn-period-detail .cn-detail-table th,
        .cn-period-detail .cn-detail-table td { padding: 12px 10px; white-space: normal; overflow-wrap: normal; word-break: normal; min-width: 0; }
        .cn-period-detail .cn-detail-table .cn-number, .cn-period-detail .cn-nowrap { white-space: nowrap; overflow-wrap: normal; word-break: normal; font-variant-numeric: tabular-nums; }
        .cn-period-detail .cn-detail-table .cn-number { min-width: 105px; }
        .cn-period-detail .cn-period-collections { min-width: 1080px; table-layout: auto; }
        .cn-period-collections th:first-child { width: 25%; min-width: 230px; }
        .cn-period-collections th:last-child { width: 20%; min-width: 185px; }
        .cn-period-collections th:not(:first-child):not(:last-child) { text-align: right; min-width: 105px; }
        .cn-period-collections td { vertical-align: top; }
        .cn-period-person { color: var(--cn-muted); font-size: 12px; margin-top: 5px; }
        .cn-payment-pending { background: var(--cn-warning-bg); }
        .cn-period-row-status { display: block; font-weight: 600; }
        .cn-period-row-detail { margin-top: 8px; font-size: 13px; }
        .cn-period-row-detail summary { cursor: pointer; color: var(--cn-primary); }
        .cn-period-row-detail div { margin-top: 8px; }
        .cn-period-difference-filter { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 12px 0; color: var(--cn-muted); font-size: 13px; }
        .cn-period-difference-filter label { display: flex; align-items: center; gap: 8px; color: var(--cn-text); cursor: pointer; }
        .cn-period-difference-filter input { margin: 0; }
        .cn-period-secondary { margin: 18px 0 24px; padding: 14px 16px; background: var(--cn-muted-surface); color: var(--cn-text); border: 1px solid var(--cn-border); border-radius: 10px; }
        .cn-period-secondary summary { cursor: pointer; font-weight: 600; }
        .cn-period-secondary p, .cn-period-table-note { color: var(--cn-muted); font-size: 13px; }
        .cn-period-controls { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 30px; margin-top: 18px; }
        .cn-period-controls > div { display: flex; justify-content: space-between; gap: 15px; }
        .cn-period-controls dd { white-space: nowrap; }
        .cn-period-notice { padding: 12px 16px; border-left: 3px solid var(--cn-warning); background: var(--cn-warning-bg); color: var(--cn-text); }
        .cn-kpis.cn-period-summary { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .cn-period-summary .cn-kpi-value { white-space: nowrap; font-size: clamp(20px, 2vw, 28px); }
        .cn-period-summary p { margin: 8px 0 0; color: var(--cn-muted); font-size: 12px; }
        .cn-period-summary .cn-kpi-label { font-size: 12px; }
        .cn-period-detail h4 { font-size: 16px; }
        .cn-period-scroll-hint { display: none; color: var(--cn-muted); font-size: 12px; }
        @media (max-width: 1200px) { .cn-period-scroll-hint { display: block; } }
        @media (max-width: 900px) { .cn-kpis.cn-period-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        @media (max-width: 540px) { .cn-period-controls { grid-template-columns: 1fr; } .cn-kpis.cn-period-summary { grid-template-columns: 1fr; } }
        .cn-period-detail .cn-detail-table button { font-size: inherit; white-space: normal; overflow-wrap: anywhere; }
        .cn-period-detail .cn-period-applications { table-layout: auto; min-width: 1000px; }
        .cn-period-applications th:nth-child(1) { width: 9%; }
        .cn-period-applications th:nth-child(2) { width: 23%; }
        .cn-period-applications th:nth-child(3),
        .cn-period-applications th:nth-child(4),
        .cn-period-applications th:nth-child(5) { width: 11%; }
        .cn-period-applications th:nth-child(6) { width: 20%; }
        .cn-period-applications th:nth-child(7) { width: 15%; }
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
        .cn-cash-distribution .cn-detail-table tfoot th { font-weight: 700; color: var(--cn-text); }
        .cn-text-link { border: 0; background: none; color: var(--cn-primary); font-weight: 600; padding: 0; }
        .cn-footnote { margin-top: 12px; } .cn-dialog { max-height: 70vh; overflow: auto; }
        .cn-dialog h4 { font-size: 13px; font-weight: 700; margin: 20px 0 8px; }
        .cn-dialog-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
        @media(max-width: 1100px) { .cn-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); } }
        @media(max-width: 650px) { .cn-kpis { grid-template-columns: repeat(2, minmax(120px, 1fr)); } .cn-intro { flex-wrap: wrap; } }
    </style>`;
}
})();
