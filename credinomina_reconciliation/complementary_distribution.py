"""Explicit manual scopes for a single, shared complementary cash claim."""
from credinomina_reconciliation.parsers import clean_text


def company_scope(item):
    companies = {clean_text(item.get("employer"))} - {""}
    if item.get("generic_distribution"):
        companies.update(clean_text(row.get("employer")) for row in item.get("distribution_companies") or [])
    return companies - {""}


def attach_company_scopes(items):
    import frappe
    generic = {item.name: item for item in items if item.get("generic_distribution")}
    if not generic:
        return
    for item in generic.values():
        item.distribution_companies = []
    for row in frappe.get_all("CN Paying Employer", filters={
        "parenttype": "CN Complementary Item", "parentfield": "distribution_companies",
        "parent": ["in", list(generic)],
    }, fields=["parent", "employer"], limit_page_length=0):
        generic[row.parent].distribution_companies.append(row)


def shared_company_pools():
    """Connect balance/locking pools, never extend a payer's authorization."""
    import frappe
    items = frappe.get_all("CN Complementary Item", filters={
        "docstatus": 1, "generic_distribution": 1,
        "category": ["in", ["Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"]],
    }, fields=["name", "employer", "generic_distribution"], limit_page_length=0)
    attach_company_scopes(items)
    return [company_scope(item) for item in items]


def validate_distribution(item):
    import frappe
    from frappe import _
    companies = [row.employer for row in item.get("distribution_companies") or []]
    if not item.get("generic_distribution"):
        if companies:
            frappe.throw(_("Las empresas de distribución solo se usan en una partida genérica manual."))
        return
    if item.category not in {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"}:
        frappe.throw(_("La distribución genérica solo admite partidas de depósito, no ajustes de aplicación, compensaciones ni saldos a favor."))
    if not item.employer:
        frappe.throw(_("Indique la empresa de origen de la partida genérica."))
    if any(item.get(field) for field in ("client_number", "loan_number", "installment_number")):
        frappe.throw(_("Una partida genérica no debe identificar un solo cliente o crédito; detalle su reparto en los destinos del depósito."))
    if any(not company or company == item.employer for company in companies) or len(companies) != len(set(companies)):
        frappe.throw(_("No repita empresas de distribución ni la empresa de origen, que ya está incluida."))
    if item.period and companies:
        frappe.throw(_("Deje el período vacío cuando la partida genérica abarque varias empresas."))


def guard_closed_distributions(item):
    """A periodless shared claim still participates in its deposits' closed periods."""
    if not item.get("generic_distribution"):
        return
    import frappe
    from frappe import _
    from credinomina_reconciliation.remittance_periods import selected_periods
    parents = set(frappe.get_all("CN Remittance Target", filters={
        "complementary_item": item.name, "docstatus": 1,
    }, pluck="parent", limit_page_length=0))
    for name in sorted(parents):
        deposit = frappe.get_doc("CN Remittance Allocation", name)
        if deposit.docstatus != 1:
            continue
        deposit._assert_open_related_periods()
        for period in selected_periods(deposit):
            if frappe.db.get_value("CN Reconciliation Period", period, "status") == "Cerrado":
                frappe.throw(_("La partida genérica participa en el depósito {0} de un período cerrado. Reabra el período antes de cancelar la partida.").format(name))
