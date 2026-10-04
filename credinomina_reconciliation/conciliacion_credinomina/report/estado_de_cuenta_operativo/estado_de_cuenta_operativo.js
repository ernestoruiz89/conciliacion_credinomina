frappe.query_reports["Estado de Cuenta Operativo"] = {
    filters: [
        { fieldname: "view_mode", label: __("Vista"), fieldtype: "Select", options: "Resumen\nDetalle", default: "Resumen", reqd: 1 },
        { fieldname: "position_type", label: __("Tipo"), fieldtype: "Select", options: "\nCobranza\nAplicación\nPartida complementaria" },
        { fieldname: "operational_status", label: __("Estado de conciliación"), fieldtype: "Autocomplete",
            description: __("Vacío: todos. Seleccione o escriba el estado exacto de la columna Estado de conciliación."),
            options: ["Conciliado", "Conciliada", "Parcial", "Pendiente", "Pendiente de confirmar", "Documentado",
                "Revisar distribución", "Sin conversión US$", "Detalle de empresa pendiente", "Detalle de empresa por aclarar",
                "Diferencia entre aplicación y depósito; requiere revisión", "Diferencia cambiaria en revisión",
                "Pendiente de detalle de la empresa", "Cobranza no deducida; revisar primera conciliación",
                "Depositado por la empresa; aplicación parcial en core", "Depósito parcial",
                "Aplicación parcial en core; depósito pendiente", "Aplicado en core; depósito pendiente",
                "Deducido; pendiente de aplicar en core", "Pendiente de conciliacion",
                "Registro contable verificado", "No conciliatoria", "Vigente", "Revertida"] },
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro Cedula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Credito"), fieldtype: "Data" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
        { fieldname: "only_open", label: __("Solo pendientes"), fieldtype: "Check", default: 0 },
    ],
    formatter(value, row, column, data, default_formatter) {
        const summary_fields = ["pending_usd", "company_credit_usd", "client_credit_usd", "balance_usd"];
        if (data && summary_fields.includes(column.fieldname) && data[column.fieldname] == null) {
            return `<span class="text-muted" title="${__("Importe sin determinar; revise el detalle y la conversión de moneda.")}">—</span>`;
        }
        return default_formatter(value, row, column, data);
    },
};
