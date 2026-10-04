frappe.query_reports["Antiguedad de Saldos por Empresa"] = {
    filters: [
        { fieldname: "as_of_date", label: __("Fecha para antigüedad"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "from_month", label: __("Mes de cobranza desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Mes de cobranza hasta"), fieldtype: "Date" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "reconciliation_mode", label: __("Modalidad"), fieldtype: "Select", options: "\nHistorica\nOperativa" },
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro. Cédula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Crédito"), fieldtype: "Data" },
        { fieldname: "balance_type", label: __("Tipo de saldo"), fieldtype: "Select", default: "Aplicado pendiente de depósito", options: "Aplicado pendiente de depósito\nCobranza no deducida (informativo)\nDeducido sin depósito asignado\nDetalle de empresa pendiente\nDetalle de empresa por aclarar" },
    ],
};
