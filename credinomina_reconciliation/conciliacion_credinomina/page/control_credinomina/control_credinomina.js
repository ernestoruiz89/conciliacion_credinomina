(function () {
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
            currentData = response.message || { periods: [], totals: {}, open_deposits: [] };
            render(currentData);
        }).catch(() => {
            $root.html(`${styles()}<div class="cn-empty">${esc(__("No se pudo cargar el control."))}</div>`);
        });
    }

    function render(data) {
        const periods = data.periods || [];
        const totals = data.totals || {};
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
            [__("CxC de empresas"), totals.employer_gap_usd, "gap"],
            [__("Detalle pendiente"), totals.pending_detail_usd, "pending"],
            [__("Histórico sin depósito"), totals.historical_pending_usd, "pending"],
            [__("Saldo a favor documentado"), totals.documented_credit_usd, "surplus"],
            [__("Sin clasificar"), totals.unclassified_deposit_usd, "unclassified"],
        ].map(([label, value, kind]) => `
            <div class="cn-kpi cn-kpi-${kind}">
                <div class="cn-kpi-label">${esc(label)}</div>
                <div class="cn-kpi-value">${money(value)}</div>
            </div>
        `).join("");
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
                    const monthRemitted = ordered.reduce((sum, item) => sum + Number(item.remitted_usd || 0), 0);
                    const monthCompared = ordered.reduce((sum, item) => sum + Number(
                        item.reconciliation_mode === "Historica" ? item.applied_usd || 0 : item.deducted_usd || 0
                    ), 0);
                    const monthState = ordered.every((item) => ["conciliado", "historico_conciliado"].includes(item.control_state)) ? "conciliado" :
                        ordered.some((item) => ["diferencia", "excedente", "historico_excedente"].includes(item.control_state)) ? "diferencia" :
                        ordered.some((item) => ["parcial", "historico_parcial"].includes(item.control_state)) ? "parcial" : "en_transito";
                    return `<td class="cn-cell cn-${esc(ordered.length === 1 ? ordered[0].control_state : monthState)}">
                        ${ordered.length > 1 ? `<div class="cn-cell-summary">${esc(__("Total del mes"))}: ${money(monthRemitted)} / ${money(monthCompared)}</div>` : ""}
                        ${ordered.map((period) => `<button type="button" class="cn-cell-button" data-period="${esc(period.name)}">
                            <span class="cn-cell-cycle">${esc(period.reconciliation_mode === "Historica" ? historicalLabel(period) : period.collection_cycle || __("Mensual"))}</span>
                            <span class="cn-cell-amount">${money(period.remitted_usd)} / ${money(period.reconciliation_mode === "Historica" ? period.applied_usd : period.deducted_usd)}</span>
                            <span class="cn-cell-sub">${esc(period.reconciliation_mode === "Historica" ? __("Depósito / aplicación histórica") : __("Remitido / deducido"))}</span>
                            <span class="cn-badge">${esc(stateLabel(period.control_state))}${period.deduction_basis === "Depósito coincidente" ? ` · ${esc(__("Deducción inferida"))}` : ""}</span>
                            ${(period.rounding_movements || []).length ? `<span class="cn-cell-credit">${esc(__("Ajuste menor"))}: ${signedMoney(period.rounding_adjustment_usd)}</span>` : ""}
                            ${Number(period.historical_pending_usd) > 0.00005 ? `<span class="cn-cell-gap">${esc(__("Sin depósito"))}: ${money(period.historical_pending_usd)}</span>` : ""}
                            ${Number(period.employer_gap_usd) > 0.00005 ? `<span class="cn-cell-gap">${esc(__("CxC"))}: ${money(period.employer_gap_usd)}</span>` : ""}
                            ${Number(period.documented_credit_usd) > 0.00005 ? `<span class="cn-cell-credit">${esc(__("Saldo a favor"))}: ${money(period.documented_credit_usd)}</span>` : ""}
                            ${Number(period.unclassified_deposit_usd) > 0.00005 ? `<span class="cn-cell-gap">${esc(__("Sin clasificar"))}: ${money(period.unclassified_deposit_usd)}</span>` : ""}
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
                <td>${esc(row.event_date)}</td>
                <td><button class="cn-text-link" type="button" data-import="${esc(row.parent)}">${esc(row.reference)}</button></td>
                <td>${esc(row.loan_number)}</td>
                <td class="cn-number">${money(row.amount)}</td>
                <td>${esc(row.match_reason)}</td>
            </tr>`).join("")}</tbody></table></div>` : "";
        const depositTable = deposits.length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table">
                <thead><tr><th>${esc(__("Fecha"))}</th><th>${esc(__("Empresa"))}</th><th>${esc(__("Referencia"))}</th><th>${esc(__("Comprobante"))}</th><th>${esc(__("Depósito original"))}</th><th>${esc(__("Distribuido US$"))}</th><th>${esc(__("Sin distribuir US$"))}</th><th>${esc(__("Saldo a favor documentado US$"))}</th><th>${esc(__("Sin clasificar US$"))}</th><th>${esc(__("Distribución"))}</th></tr></thead>
                <tbody>${deposits.map((deposit) => `<tr>
                    <td>${esc(deposit.event_date)}</td>
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
        ` : `<div class="cn-empty">${esc(__("No hay depósitos pendientes de distribuir en este año."))}</div>`;
        $root.html(`${styles()}
            <div class="cn-intro">
                <div><h2>${esc(__("Matriz mensual de conciliación"))}</h2><p>${esc(__("Seleccione una celda para ver aplicaciones, depósitos y, desde septiembre de 2026, deducciones de planilla."))}</p></div>
                <span class="cn-year">${esc(String(year))}</span>
            </div>
            <div class="cn-kpis">${cards}</div>
            <section class="cn-panel"><div class="cn-panel-head"><h3>${esc(__("Empresas por mes de conciliación"))}</h3><span>${periods.length} ${esc(__("períodos"))}</span></div>${matrix}</section>
            ${unassigned.length ? `<section class="cn-panel"><div class="cn-panel-head"><h3>${esc(__("Aplicaciones históricas sin período"))}</h3><span>${unassigned.length} ${esc(__("filas"))}</span></div>${unassignedTable}</section>` : ""}
            ${employerField.get_value() ? "" : `<section class="cn-panel"><div class="cn-panel-head"><h3>${esc(__("Depósitos pendientes de distribuir"))}</h3><span>${deposits.length} ${esc(__("depósitos"))}</span></div>${depositTable}</section>`}
            <p class="cn-footnote">${esc(__("Los saldos a favor y los depósitos sin clasificar no se aplican automáticamente a créditos. Las cifras están expresadas en US$."))}</p>
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
                <td>${esc(row.event_date)}</td>
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
                <th>${esc(__("Cliente"))}</th><th>${esc(__("Crédito / cuota"))}</th><th>${esc(__("Deducido"))}</th>
                <th>${esc(__("Aplicado"))}</th><th>${esc(__("Complementario"))}</th><th>${esc(__("Remitido"))}</th><th>${esc(__("Ajuste US$"))}</th><th>${esc(__("Depósitos"))}</th><th>${esc(__("Excepción / antecedente"))}</th><th>${esc(__("Estado"))}</th>
            </tr></thead><tbody>${period.rows.map((row) => `<tr>
                <td>${esc(row.client_number)} · ${esc(row.client_name)}</td>
                <td>${esc(row.loan_number)} / ${esc(row.installment_number)}</td>
                <td class="cn-number">${money(row.deducted_usd)}</td>
                <td class="cn-number">${money(row.applied_usd)}</td>
                <td class="cn-number">${money(row.complementary_usd)}</td>
                <td class="cn-number">${money(row.remitted_usd)}</td>
                <td class="cn-number">${signedMoney(row.rounding_adjustment_usd)}</td>
                <td>${allocationLines(row.remittance_detail)}</td>
                <td>${row.inherited_exception_comment ? `${esc(__("Trasladada: "))}${esc(row.inherited_exception_comment)}` : row.first_exception_comment ? `${esc(__("Primera conciliación: "))}${esc(row.first_exception_comment)}` : esc(row.application_comment || "—")}</td>
                <td>${esc(row.application_status || row.deduction_status)}${row.deduction_status === "Inferida por depósito" ? `<br><span class="cn-inherited-note">${esc(__("Deducción inferida, sin detalle de planilla"))}</span>` : ""}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="cn-empty">${esc(__("Sin detalle de cobranza."))}</div>`;
        const exceptions = (period.exceptions || []).length ? `
            <div class="cn-list-scroll"><table class="cn-detail-table"><thead><tr><th>${esc(__("Cliente"))}</th><th>${esc(__("Crédito"))}</th><th>${esc(__("Motivo"))}</th><th>${esc(__("Importe"))}</th></tr></thead><tbody>
            ${period.exceptions.map((item) => `<tr><td>${esc(item.client_number)}</td><td>${esc(item.loan_number)}</td><td>${esc(item.exception_type)} · ${esc(item.description)}</td><td class="cn-number">${money(item.amount_usd)}</td></tr>`).join("")}
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
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("CxC empresa"))}</div><div class="cn-kpi-value">${money(period.employer_gap_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-surplus"><div class="cn-kpi-label">${esc(__("Saldo a favor"))}</div><div class="cn-kpi-value">${money(period.documented_credit_usd)}</div></div>
                    <div class="cn-kpi cn-kpi-gap"><div class="cn-kpi-label">${esc(__("Sin clasificar"))}</div><div class="cn-kpi-value">${money(period.unclassified_deposit_usd)}</div></div>
                    `}
                </div>
                ${historical ? `<p>${esc(__("Histórico: no se infieren deducciones de planilla ni cuentas por cobrar a la empresa."))}</p><h4>${esc(__("Aplicaciones contra depósitos"))}</h4>${historicalTable}${(period.exceptions || []).length ? `<h4>${esc(__("Incidencias documentadas"))}</h4>${exceptions}` : ""}` : `<h4>${esc(__("Detalle de cobranza"))}</h4>${rowTable}<h4>${esc(__("Excepciones abiertas"))}</h4>${exceptions}`}
                <h4>${esc(__("Movimientos de conciliación"))}</h4>${movements}
                <h4>${esc(__("Excedentes de depósito"))}</h4>${surplus}
            </div>
        `);
    }

    $root.on("click", "[data-period]", function () { showPeriod($(this).attr("data-period")); });
    $root.on("click", "[data-import]", function () {
        frappe.set_route("Form", "CN Source Import", $(this).attr("data-import"));
    });
    $root.on("click", "[data-remittance]", function () {
        frappe.set_route("Form", "CN Remittance Allocation", $(this).attr("data-remittance"));
    });
    $root.on("click", "[data-movement]", function () {
        frappe.set_route("Form", "CN Reconciliation Movement", $(this).attr("data-movement"));
    });
    page.add_button(__("Registrar depósito"), () => frappe.new_doc("CN Remittance Allocation"));
    page.add_button(__("Documentar excedente"), () => frappe.new_doc("CN Deposit Surplus"));
    page.set_primary_action(__("Actualizar"), refresh);
    refresh();
};

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
    })[state] || __("Pendiente");
}

function cycleOrder(cycle) {
    return ({ "Primera quincena": 1, "Segunda quincena": 2, "Mensual": 3 })[cycle] || 4;
}

function historicalSortDate(period) {
    return period.historical_application_date || period.historical_start_date || period.payroll_month || "";
}

function historicalLabel(period) {
    if (period.historical_scope === "Fecha exacta") {
        return `${__("Histórico")} · ${period.historical_application_date || ""}`;
    }
    if (period.historical_scope === "Rango de fechas") {
        return `${__("Histórico")} · ${period.historical_start_date || ""} – ${period.historical_end_date || ""}`;
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
        .cn-matrix-scroll, .cn-list-scroll { overflow-x: auto; }
        .cn-matrix { width: 100%; min-width: 1500px; border-collapse: separate; border-spacing: 0; table-layout: fixed; }
        .cn-matrix th { background: #1e293b; color: white; padding: 10px 6px; font-size: 11px; text-transform: uppercase; text-align: center; }
        .cn-matrix th.cn-company { width: 200px; text-align: left; position: sticky; left: 0; z-index: 2; }
        .cn-matrix tbody th.cn-company { background: white; color: #334155; border-right: 2px solid #cbd5e1; border-bottom: 1px solid #e2e8f0; text-transform: none; font-size: 12px; }
        .cn-matrix td { border-bottom: 1px solid #e2e8f0; border-right: 1px solid #f1f5f9; vertical-align: top; padding: 0; }
        .cn-cell-button { width: 100%; border: 0; background: transparent; text-align: left; padding: 9px 7px; min-height: 88px; }
        .cn-cell-button + .cn-cell-button { border-top: 1px dashed #cbd5e1; }
        .cn-cell-cycle { display: block; font-size: 10px; font-weight: 700; color: #475569; margin-bottom: 3px; }
        .cn-cell-summary { padding: 6px 7px; font-size: 10px; font-weight: 700; color: #1e293b; border-bottom: 1px solid #cbd5e1; }
        .cn-cell-button:hover { filter: brightness(.97); } .cn-cell-amount { display: block; font-weight: 700; font-size: 11px; white-space: nowrap; }
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
        .cn-empty-cell { text-align: center; padding: 28px 4px !important; color: #cbd5e1; }
        .cn-empty, .cn-loading { padding: 24px; text-align: center; color: #94a3b8; font-size: 12px; }
        .cn-detail-table { width: 100%; border-collapse: collapse; font-size: 11px; }
        .cn-detail-table th { text-align: left; background: #f8fafc; color: #64748b; text-transform: uppercase; font-size: 9px; letter-spacing: .04em; }
        .cn-detail-table th, .cn-detail-table td { padding: 9px 10px; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
        .cn-detail-table td:nth-child(3) { white-space: normal; min-width: 100px; }
        .cn-detail-table .cn-number { text-align: right; font-weight: 600; }
        .cn-text-link { border: 0; background: none; color: #2563eb; font-weight: 600; padding: 0; }
        .cn-footnote { margin-top: 12px; } .cn-dialog { max-height: 70vh; overflow: auto; }
        .cn-dialog h4 { font-size: 13px; font-weight: 700; margin: 20px 0 8px; }
        .cn-dialog-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); }
        @media(max-width: 1100px) { .cn-kpis { grid-template-columns: repeat(3, minmax(130px, 1fr)); } }
        @media(max-width: 650px) { .cn-kpis { grid-template-columns: repeat(2, minmax(120px, 1fr)); } }
    </style>`;
}
})();
