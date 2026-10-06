frappe.query_reports["Antiguedad de Saldos"] = {
    filters: [
        { fieldname: "historical_cutoff", label: __("Corte histórico"), fieldtype: "Check", default: 0,
            description: __("Reconstruye la CxC a la fecha para antigüedad; no descuenta operaciones posteriores.") },
        { fieldname: "as_of_date", label: __("Fecha para antigüedad"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
        { fieldname: "from_month", label: __("Mes desde"), fieldtype: "Date" },
        { fieldname: "to_month", label: __("Mes hasta"), fieldtype: "Date" },
        { fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer" },
        { fieldname: "reconciliation_mode", label: __("Modalidad"), fieldtype: "Select", options: "\nHistorica\nOperativa" },
        { fieldname: "client_number", label: __("Nro. Cliente"), fieldtype: "Data" },
        { fieldname: "national_id", label: __("Nro. Cédula"), fieldtype: "Data" },
        { fieldname: "loan_number", label: __("Nro. Crédito"), fieldtype: "Data" },
        { fieldname: "balance_type", label: __("Tipo de saldo"), fieldtype: "Select", default: "CxC total", options: "CxC total\nAplicado pendiente de depósito\nCxC por ajustes\nCobranza no deducida (informativo)\nDeducido sin depósito asignado\nDetalle de empresa pendiente\nDetalle de empresa por aclarar" },
    ],
};
