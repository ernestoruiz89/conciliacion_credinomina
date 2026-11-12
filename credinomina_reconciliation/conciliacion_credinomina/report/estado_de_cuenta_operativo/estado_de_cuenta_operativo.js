frappe.query_reports["Estado de Cuenta Operativo"] = {
    filters: [
        { fieldname: "position_type", label: __("Tipo"), fieldtype: "Select", options: "\nAplicaciones, depósitos y partidas\nCobranza\nAplicación\nDepósito con saldo pendiente\nPartida complementaria\nCxC por ajuste", default: "Aplicaciones, depósitos y partidas",
            description: __("La vista inicial compara aplicaciones con depósitos y partidas complementarias. Elija otro tipo para consultar cobranza o CxC. Vacío muestra todos los tipos; sus importes se mantienen separados.") },
        { fieldname: "detailed_columns", label: __("Más columnas"), fieldtype: "Check", default: 0,
            description: __("Muestra el efectivo asignado, el pendiente según detalle, clasificación, gestión y observaciones. La vista compacta reúne depósitos y complementarias netas en Asignado US$.") },
        { fieldname: "only_open", label: __("Mostrar solo pendientes"), fieldtype: "Check", default: 1,
            description: __("Oculta las partidas conciliadas, aunque tengan gestión o registro contable pendiente.") },
        { fieldname: "operational_status", label: __("Estado de conciliación"), fieldtype: "Autocomplete",
            description: __("Vacío: todos. Seleccione o escriba el estado exacto de la columna Estado de conciliación."),
            options: ["Conciliado", "Parcial", "Pendiente",
                "Revisar distribución", "Sin conversión US$",
                "Diferencia entre aplicación y depósito; requiere revisión", "Diferencia cambiaria en revisión",
                "Conforme según cobranza", "Aplicación insuficiente", "Aplicación en exceso", "Revisar base de comparación",
                "Cancelada", "Revertida", "Parcialmente revertida", "Vinculado a depósito", "Pendiente de cobro", "Parcialmente cobrada", "Cobrada", "Revisar cobros"] },
        { fieldname: "category", label: __("Categoría"), fieldtype: "Autocomplete", options: ["Por clasificar", "Ajuste de conciliación", "Ajuste de aplicación", "Compensación entre partidas", "Otros ingresos", "Cobranza administrativa", "Cobro de CxC", "Saldo a favor del cliente", "Saldo a favor de la empresa", "Diferencia por tolerancia"] },
        { fieldname: "subcategory", label: __("Subcategoría"), fieldtype: "Link", options: "CN Complementary Subcategory" },
        { fieldname: "reason_type", label: __("Motivo"), fieldtype: "Select", options: "\nError de la empresa\nPago adicional no informado\nPor refinanciamiento\nPor cancelación\nOtro por aclarar" },
        { fieldname: "accounting_status", label: __("Registro contable"), fieldtype: "Select", options: "\nPendiente de registro\nAsiento informado\nImportada del core\nRegistrada\nNo requiere registro" },
        { fieldname: "management_status", label: __("Gestión externa"), fieldtype: "Autocomplete", options: ["Pendiente", "Parcialmente resuelto", "Resuelto", "Pendiente de cobro", "Parcialmente cobrada", "Revisar cobros"] },
        { fieldname: "treatment_status", label: __("Tratamiento"), fieldtype: "Autocomplete", options: ["Documentado", "Vigente", "Registro contable verificado", "Por aclarar (no conciliatoria)", "Pendiente de confirmar", "Conciliada", "Parcial", "Pendiente", "Revisar distribución", "Revertida", "Parcialmente revertida"] },
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro Cedula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Credito"), fieldtype: "Data" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "cutoff_date", label: __("Fecha de corte histórico"), fieldtype: "Date",
            description: __("Reconstruye los saldos a esa fecha con los vínculos vigentes. Vacío muestra los saldos actuales. Desde y Hasta limitan las fechas de origen.") },
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena\nFecha exacta" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
    ],
    onload(report) {
        report.page.wrapper?.off("click.cnAssignedDetails").on("click.cnAssignedDetails", "[data-cn-assigned]", event => {
            event.preventDefault();
            const token = event.currentTarget.getAttribute("data-cn-assigned");
            const identity = JSON.parse(decodeURIComponent(token));
            showOperationalAssignedDetails(report, identity);
        });
        const advanced = ["client_number", "national_id", "loan_number", "collection_cycle", "category", "subcategory", "reason_type", "accounting_status", "management_status", "treatment_status"];
        let expanded = false;
        const display = () => advanced.forEach(name => {
            const control = report.get_filter(name);
            control.$wrapper.toggle(expanded || Boolean(control.get_value()));
        });
        report.page.add_inner_button(__("Buscar cliente"), () => showOperationalClientSearch(report, display));
        const button = report.page.add_inner_button(__("Más filtros"), () => {
            expanded = !expanded;
            button.text(__(expanded ? "Menos filtros" : "Más filtros"));
            display();
        });
        display();
    },
    formatter(value, row, column, data, default_formatter) {
        let formatted = column.fieldtype === "Currency" && data && data[column.fieldname] == null
            ? `<span class="text-muted" title="${__("Importe sin determinar o no aplicable a este tipo de fila")}">—</span>`
            : default_formatter(value, row, column, data);
        if (column.fieldname === "assigned_usd" && data?.position_type === "Aplicación"
            && !data.is_total_row && data.source_rows && data.assigned_usd != null) {
            const identity = encodeURIComponent(JSON.stringify({source_rows: data.source_rows,
                period: data.period || "", collection_row_id: data.collection_row_id || ""}));
            formatted = `<a href="#" data-cn-assigned="${frappe.utils.escape_html(identity)}" title="${frappe.utils.escape_html(__("Ver depósitos y partidas complementarias asignados"))}">${formatted}</a>`;
        }
        return data && data.is_total_row ? `<strong>${formatted}</strong>` : formatted;
    },
};

async function showOperationalAssignedDetails(report, identity) {
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const amount = value => value == null ? "—" : esc(format_currency(value, "USD", 2));
    const routes = {"CN Remittance Allocation": "cn-remittance-allocation", "CN Complementary Item": "cn-complementary-item"};
    let closed = false, sequence = 0;
    // Capture the filters of the displayed result, including its historical cut.
    const filters = report.get_filter_values();
    const dialog = new frappe.ui.Dialog({title: __("Detalle del importe asignado"), size: "extra-large",
        fields: [{fieldname: "details", fieldtype: "HTML"}],
        secondary_action_label: __("Actualizar"), secondary_action: () => load(),
        primary_action_label: __("Cerrar"), primary_action: () => dialog.hide(),
        onhide() { closed = true; },
    });
    function table(rows, total, includeTotal = true) {
        return `<div class="table-responsive"><table class="table table-bordered"><thead><tr>${
            ["Documento", "Tipo de vínculo", "Estado", "Importe US$"].map(label => `<th>${esc(__(label))}</th>`).join("")}</tr></thead><tbody>${
            rows.map(row => `<tr><td>${routes[row.doctype] && row.document
                ? `<a href="/app/${routes[row.doctype]}/${encodeURIComponent(row.document)}" target="_blank" rel="noopener noreferrer">${esc(row.document)}</a>`
                : esc(__("Documento no disponible"))}</td><td>${esc(__(row.kind || ""))}</td><td>${esc(__(row.state || ""))}</td><td class="text-right">${amount(row.amount_usd)}</td></tr>`).join("")
                || `<tr><td colspan="4">${esc(__("No hay documentos identificables para esta selección."))}</td></tr>`}</tbody>${includeTotal
                    ? `<tfoot><tr><th colspan="3">${esc(__("Total del desglose"))}</th><th class="text-right">${amount(total)}</th></tr></tfoot>` : ""}</table></div>`;
    }
    async function load() {
        const current = ++sequence, wrapper = dialog.fields_dict.details.$wrapper;
        wrapper.html(`<p class="text-muted">${esc(__("Consultando vínculos…"))}</p>`);
        try {
            const response = await frappe.call({method: "credinomina_reconciliation.operational_assigned_details.get_details",
                args: {...identity, filters: JSON.stringify(filters)}, silent: true});
            if (closed || current !== sequence) return;
            const data = response.message || {};
            wrapper.html(`<p><strong>${esc(data.client_name)}</strong>${data.cutoff_date
                ? ` · ${esc(__("Corte histórico"))}: ${esc(frappe.datetime.str_to_user(data.cutoff_date))}` : ""}</p>
                <p><strong>${esc(__("Asignado US$"))}: ${amount(data.assigned_usd)}</strong></p>
                ${table(data.rows || [], data.total_usd)}
                ${(data.notes || []).map(note => `<p class="text-muted">${esc(__(note))}</p>`).join("")}
                ${data.adjustments?.length ? `<h5>${esc(__("Otros vínculos de la aplicación"))}</h5>
                    <p class="text-muted">${esc(__("Los ajustes de aplicación ya están descontados del aplicado neto. Las complementarias compartidas ya están incluidas en la cobertura. Estos vínculos no se suman de nuevo a Asignado."))}</p>
                    ${table(data.adjustments, null, false)}` : ""}`);
        } catch (_) {
            if (!closed && current === sequence) wrapper.html(`<p class="text-danger">${esc(__("No se pudo consultar el detalle. Actualice el reporte y revise sus permisos antes de intentar nuevamente."))}</p>`);
        }
    }
    dialog.show();
    await load();
}

function showOperationalClientSearch(report, displayFilters) {
    let dialog, applying = false, closed = false;
    dialog = new frappe.ui.Dialog({
        title: __("Buscar cliente"),
        fields: [
            {fieldname: "employer", fieldtype: "Link", options: "CN Employer", label: __("Empresa"),
                default: report.get_filter_value("employer") || "",
                description: __("Deje vacío para buscar en todas las empresas que puede consultar."),
                onchange() { dialog?.set_value("client", ""); }},
            {fieldname: "client", fieldtype: "Link", options: "CN Client", label: __("Cliente"), reqd: 1,
                only_select: true,
                description: __("Escriba parte del nombre, número de cliente o cédula y seleccione el cliente."),
                get_query() {
                    const employer = dialog?.get_value("employer");
                    return {filters: employer ? {employer} : {}};
                }},
        ],
        primary_action_label: __("Filtrar reporte"),
        async primary_action(values) {
            if (applying || !values.client) return;
            applying = true;
            dialog.get_primary_btn().prop("disabled", true);
            try {
                const response = await frappe.db.get_value("CN Client", values.client, ["client_number", "employer"]);
                if (closed || dialog.get_value("client") !== values.client
                    || (dialog.get_value("employer") || "") !== (values.employer || "")) return;
                const client = response.message || {};
                if (!client.client_number || !client.employer || (values.employer && client.employer !== values.employer)) {
                    frappe.msgprint(__("No se pudo identificar el número de cliente y su empresa. Revise la selección."));
                    return;
                }
                const filters = {national_id: "", loan_number: "", employer: client.employer,
                    client_number: String(client.client_number)};
                // Wait for all controls before querying, even if the final
                // field already has the selected value and emits no change.
                const previous = report._no_refresh;
                report._no_refresh = true;
                try {
                    for (const [field, value] of Object.entries(filters)) {
                        await report.get_filter(field).set_value(value);
                    }
                } finally {
                    report._no_refresh = previous;
                }
                displayFilters();
                dialog.hide();
                await report.refresh();
            } catch (error) {
                if (!closed) frappe.msgprint(__("No se pudo aplicar el filtro del cliente. Vuelva a intentar."));
            } finally {
                applying = false;
                dialog.get_primary_btn().prop("disabled", false);
            }
        },
        onhide() { closed = true; },
    });
    dialog.show();
}
