frappe.query_reports["Antiguedad de Saldos"] = {
    filters: [
        { fieldname: "as_of_date", label: __("Fecha para antigüedad"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "from_month", label: __("Mes de cobranza desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Mes de cobranza hasta"), fieldtype: "Date" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro. Cédula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Crédito"), fieldtype: "Data" },
        { fieldname: "balance_type", label: __("Tipo de saldo"), fieldtype: "Select", options: "\nCuota no deducida al trabajador\nDeducido sin remesa asignada\nDetalle de empresa pendiente" },
    ],
};
