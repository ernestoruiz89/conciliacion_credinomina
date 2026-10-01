frappe.query_reports["Control Mensual de Movimientos Contables"] = {
    filters: [
        {fieldname: "month", label: __("Mes"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1},
        {fieldname: "source_account", label: __("Cuenta contable"), fieldtype: "Data"},
        {fieldname: "source_currency", label: __("Moneda original"), fieldtype: "Select", options: "\nNIO\nUSD"},
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "summary", label: __("Resumen por cuenta y moneda"), fieldtype: "Check", default: 0},
    ],
    onload(report) {
        report.page.wrapper.off("click.cn_full_description", ".cn-full-description");
        report.page.wrapper.on("click.cn_full_description", ".cn-full-description", function(event) {
            event.preventDefault();
            const value = $(this).attr("data-description") || "";
            frappe.msgprint({title: __("Descripción original completa"), wide: true,
                message: `<div style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.6">${frappe.utils.escape_html(value)}</div>`});
        });
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "description" && value) {
            const escaped = frappe.utils.escape_html(String(value));
            // Raw data (and exports) keep the entire text; the modal avoids an oversized grid.
            return `<a href="#" class="cn-full-description" data-description="${escaped}" title="${__("Ver descripción completa")}">${escaped}</a>`;
        }
        if (["debit_nio", "credit_nio", "net_nio", "debit_usd", "credit_usd", "net_usd"].includes(column.fieldname) && value == null) {
            return "—";
        }
        return default_formatter(value, row, column, data);
    },
};
