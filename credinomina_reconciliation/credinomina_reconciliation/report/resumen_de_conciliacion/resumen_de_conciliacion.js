frappe.query_reports["Resumen de Conciliacion"] = {
    filters: [
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "reconciliation_mode", label: __("Modalidad"), fieldtype: "Select", options: "\nOperativa\nHistorica" },
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
        { fieldname: "status", label: __("Estado"), fieldtype: "Select", options: "\nBorrador\nCobranza cargada\nDetalle empresa cargado\nDeduccion conciliada\nDeposito conciliado\nHistorico pendiente\nHistorico parcial\nHistorico con excedente\nHistorico conciliado\nCerrado" },
    ],
};
