frappe.query_reports["Estado de Cuenta por Empresa"] = {
    filters: [
        {fieldname: "view_mode", label: __("Vista"), fieldtype: "Select", options: "Resumen\nDetalle", default: "Resumen", reqd: 1},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "from_date", label: __("Desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Hasta"), fieldtype: "Date"},
    ],
    formatter(value, row, column, data, default_formatter) {
        if (data && column.fieldtype === "Currency" && data[column.fieldname] == null) {
            return `<span class="text-muted" title="${__("Importe o signo sin determinar; revise el detalle.")}">—</span>`;
        }
        return default_formatter(value, row, column, data);
    },
};
