frappe.query_reports["Revision de Partidas Manuales"] = {
    filters: [
        {fieldname: "employer", label: __("Empresa"), fieldtype: "Link", options: "CN Employer"},
        {fieldname: "item", label: __("Partida"), fieldtype: "Link", options: "CN Complementary Item"},
        {fieldname: "from_date", label: __("Desde"), fieldtype: "Date"},
        {fieldname: "to_date", label: __("Hasta"), fieldtype: "Date"},
        {fieldname: "include_tolerance", label: __("Incluir tolerancias históricas"), fieldtype: "Check", default: 1},
        {fieldname: "show_details", label: __("Mostrar detalle de vínculos"), fieldtype: "Check", default: 0},
    ],
    onload(report) {
        report.page.wrapper.off("click.manual-item-links", ".manual-item-links")
            .on("click.manual-item-links", ".manual-item-links", function(event) {
                event.preventDefault();
                return report.set_filter_value({item: this.dataset.item, show_details: 1});
            });
    },
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "details" && data?.item) {
            return `<a href="#" class="manual-item-links" data-item="${frappe.utils.escape_html(data.item)}">${__("Ver vínculos")}</a>`;
        }
        return default_formatter(value, row, column, data);
    },
};
