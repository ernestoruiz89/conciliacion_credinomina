frappe.query_reports["Estado de Cuenta Operativo"] = {
    filters: [
        { fieldname: "position_type", label: __("Tipo"), fieldtype: "Select", options: "\nCobranza\nAplicación\nPartida complementaria\nCxC por ajuste", default: "Aplicación",
            description: __("Elija un tipo para una vista compacta. Vacío muestra todos los tipos; sus importes no se suman entre sí.") },
        { fieldname: "detailed_columns", label: __("Más columnas"), fieldtype: "Check", default: 0 },
        { fieldname: "operational_status", label: __("Estado de conciliación"), fieldtype: "Autocomplete",
            description: __("Vacío: todos. Seleccione o escriba el estado exacto de la columna Estado de conciliación."),
            options: ["Conciliado", "Parcial", "Pendiente",
                "Revisar distribución", "Sin conversión US$",
                "Diferencia entre aplicación y depósito; requiere revisión", "Diferencia cambiaria en revisión",
                "Conforme según cobranza", "Aplicación insuficiente", "Aplicación en exceso", "Revisar base de comparación",
                "Cancelada", "Revertida", "Parcialmente revertida", "Pendiente de cobro", "Parcialmente cobrada", "Revisar cobros"] },
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
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena\nFecha exacta" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
        { fieldname: "only_open", label: __("Solo pendientes"), fieldtype: "Check", default: 0 },
    ],
    onload(report) {
        const advanced = ["client_number", "national_id", "loan_number", "collection_cycle", "only_open", "category", "subcategory", "reason_type", "accounting_status", "management_status", "treatment_status"];
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
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldtype === "Currency" && data && data[column.fieldname] == null) {
            return `<span class="text-muted" title="${__("Importe sin determinar o no aplicable a este tipo de fila")}">—</span>`;
        }
        return default_formatter(value, row, column, data);
    },
};
