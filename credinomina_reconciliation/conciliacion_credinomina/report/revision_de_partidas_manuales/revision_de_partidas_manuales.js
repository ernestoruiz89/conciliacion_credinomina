frappe.query_reports["Revision de Partidas Manuales"] = {
    filters: [
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "item", label: __("Partida"), fieldtype: "Link", options: "CN Complementary Item"},
        {fieldname: "from_date", label: __("Desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Hasta"), fieldtype: "Date"},
        {fieldname: "include_tolerance", label: __("Incluir tolerancias históricas"), fieldtype: "Check", default: 1},
    ],
};
