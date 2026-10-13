frappe.query_reports["Control Mensual de Movimientos Contables"] = {
    export_hidden_cols: true,
    filters: [
        {fieldname: "from_date", label: __("Desde"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1},
        {fieldname: "to_date", label: __("Hasta"), fieldtype: "Date", default: frappe.datetime.month_end(), reqd: 1},
        {fieldname: "source_account", label: __("Cuenta contable"), fieldtype: "Data"},
        {fieldname: "source_currency", label: __("Moneda original"), fieldtype: "Select", options: "\nNIO\nUSD"},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "movement_type", label: __("Tipo de movimiento"), fieldtype: "Select",
            options: "\nAplicación de pago\nND de Aplicación de pago\nMovimiento interno\nDepósito\nPor revisar\nAplicacion\nAjuste"},
        {fieldname: "state", label: __("Estado"), fieldtype: "Autocomplete",
            description: __("Vacío: todos. Seleccione o escriba el estado exacto mostrado en el reporte."),
            options: ["Pendiente", "Conciliado", "Conciliada: depósito + ajuste", "Parcialmente conciliado", "Por revisar",
                "Pendiente de identificar", "Pendiente de revisión", "Revisar distribución", "Reversión identificada",
                "Lista para conciliar", "Ignorado para conciliación", "Compensada totalmente", "Compensada parcialmente",
                "Sin compensar", "Aplicación compensada totalmente", "Aplicación ajustada parcialmente",
                "Ajuste pendiente de completar", "Ajuste pendiente de confirmar", "Ajuste confirmado",
                "Parcial", "Sin aplicación", "Detalle pendiente",
                "Revisar detalle", "Revisar destinos", "Parcial con saldo a favor", "Saldo a favor documentado",
                "Diferencia de importe", "Falta tipo de cambio", "Ambiguo",
                "Partida cancelada; evidencia conservada", "Estado de depósito no disponible"]},
        {fieldname: "summary", label: __("Resumen por cuenta y moneda"), fieldtype: "Check", default: 0},
        {fieldname: "audit_columns", label: __("Ver auditoría"), fieldtype: "Check", default: 0,
            description: __("Muestra evidencia contable original, importes en ambas monedas y documentos vinculados.")},
        {fieldname: "category", label: __("Categoría"), fieldtype: "Autocomplete", options: ["Por clasificar", "Ajuste de conciliación", "Ajuste de aplicación", "Compensación entre partidas", "Otros ingresos", "Cobranza administrativa", "Saldo a favor del cliente", "Saldo a favor de la empresa", "Diferencia por tolerancia"]},
        {fieldname: "reason_type", label: __("Motivo"), fieldtype: "Select", options: "\nError de la empresa\nPago adicional no informado\nPor refinanciamiento\nPor cancelación\nOtro por aclarar"},
        {fieldname: "accounting_status", label: __("Registro contable"), fieldtype: "Select", options: "\nPendiente de registro\nAsiento informado\nImportada del core\nRegistrada\nNo requiere registro"},
        {fieldname: "management_status", label: __("Gestión externa"), fieldtype: "Autocomplete", options: ["Pendiente", "Parcialmente resuelto", "Resuelto"]},
        {fieldname: "treatment_status", label: __("Tratamiento"), fieldtype: "Autocomplete", options: ["Documentado", "Vigente", "Registro contable verificado", "Por aclarar (no conciliatoria)", "Pendiente de confirmar", "Conciliada", "Parcial", "Pendiente", "Revisar distribución"]},
    ],
    onload(report) {
        const advanced = ["category", "reason_type", "accounting_status", "management_status", "treatment_status"];
        let expanded = false;
        const display = () => advanced.forEach(name => {
            const control = report.get_filter(name);
            control.$wrapper.toggle(expanded || Boolean(control.get_value()));
        });
        const button = report.page.add_inner_button(__("Más filtros"), () => {
            expanded = !expanded;
            button.text(__(expanded ? "Menos filtros" : "Más filtros"));
            display();
        });
        display();
        report.page.add_inner_button(__('Exportar auditoría'), () => {
            report.export_report();
            report.export_dialog.set_value('include_hidden_columns', 1);
        });
        report.page.wrapper.off('click.cn_accounting_origin', '.cn-accounting-origin');
        report.page.wrapper.on('click.cn_accounting_origin', '.cn-accounting-origin', function(event) {
            event.preventDefault();
            const key = $(this).attr('data-origin');
            const record = report.data?.find(item => (item.source_row_id || item.complementary_item || item.remittance_allocation) === key);
            if (record) frappe.query_reports['Control Mensual de Movimientos Contables'].show_audit(record);
        });
        report.page.wrapper.off("click.cn_full_description", ".cn-full-description");
        report.page.wrapper.on("click.cn_full_description", ".cn-full-description", function(event) {
            event.preventDefault();
            const value = $(this).attr("data-description") || "";
            frappe.msgprint({title: __("Descripción original completa"), wide: true,
                message: `<div style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.6">${frappe.utils.escape_html(value)}</div>`});
        });
    },
    show_audit(record) {
        const esc = value => frappe.utils.escape_html(String(value ?? '—'));
        const links = [['accounting_import', 'CN Accounting Import', 'Importación contable'],
            ['complementary_item', 'CN Complementary Item', 'Partida complementaria'],
            ['remittance_allocation', 'CN Remittance Allocation', 'Depósito']]
            .filter(([field]) => record[field]).map(([field, doctype, label]) =>
                `<p>${esc(__(label))}: <a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(record[field])}">${esc(record[field])}</a></p>`).join('');
        const evidence = [['source_row', 'Fila original del archivo'], ['source_idx', 'Fila en la importación'],
            ['source_row_id', 'ID de fila contable'], ['voucher', 'Asiento'], ['source_account', 'Cuenta contable'],
            ['source_currency', 'Moneda original'], ['source_file', 'Archivo original'], ['source_hash', 'Huella del archivo'],
            ['evidence_key', 'Identificador de evidencia'], ['recorded_application_state', 'Estado guardado (solo auditoría)']];
        frappe.msgprint({title: __('Origen y auditoría del movimiento'), wide: true,
            message: `${links}<table class="table table-bordered">${evidence.map(([field, label]) =>
                `<tr><th>${esc(__(label))}</th><td style="overflow-wrap:anywhere">${esc(record[field] || '—')}</td></tr>`).join('')}</table>
                <h5>${__('Descripción original completa')}</h5><div style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(record.description)}</div>
                ${record.warning ? `<p class="alert alert-warning mt-3">${esc(record.warning)}</p>` : ''}`});
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === 'audit_action' && data) {
            const key = data.source_row_id || data.complementary_item || data.remittance_allocation || '';
            return `<a href="#" class="cn-accounting-origin" data-origin="${frappe.utils.escape_html(String(key))}">${__('Ver origen')}</a>`;
        }
        if (column.fieldname === "description" && value) {
            const escaped = frappe.utils.escape_html(String(value));
            // Raw data (and exports) keep the entire text; the modal avoids an oversized grid.
            return `<a href="#" class="cn-full-description" data-description="${escaped}" title="${__("Ver descripción completa")}">${escaped}</a>`;
        }
        if (["debit_nio", "credit_nio", "net_nio", "debit_usd", "credit_usd", "net_usd", 'pending_usd'].includes(column.fieldname) && value == null) {
            return column.fieldname.endsWith('_nio') && data?.source_currency === 'USD' ? '—' : __('N/D');
        }
        return default_formatter(value, row, column, data);
    },
};
