frappe.query_reports["Estado de Cuenta Operativo"] = {
    filters: [
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro Cedula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Credito"), fieldtype: "Data" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "collection_cycle", label: __("Ciclo"), fieldtype: "Select", options: "\nMensual\nPrimera quincena\nSegunda quincena" },
        { fieldname: "from_month", label: __("Desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Hasta"), fieldtype: "Date" },
        { fieldname: "only_open", label: __("Solo pendientes"), fieldtype: "Check", default: 0 },
    ],
};
