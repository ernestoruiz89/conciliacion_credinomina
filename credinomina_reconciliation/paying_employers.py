"""Explicit payer -> beneficiary authorizations; never aliases or transitive rights."""
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.remittance_periods import selected_periods


def load_payer_map():
    import frappe
    result = {}
    for row in frappe.get_all("CN Paying Employer", fields=["parent", "employer"],
                             filters={"parenttype": "CN Employer", "parentfield": "paying_for"},
                             limit_page_length=0):
        result.setdefault(row.parent, set()).add(row.employer)
    return result


def allowed_employers(payer, mapping=None):
    mapping = load_payer_map() if mapping is None else mapping
    return {payer, *mapping.get(payer, ())} - {None, ""}


def reconciliation_companies(employer, mapping=None, shared_pools=None):
    """Recompute the connected cash pool together, without extending payment rights."""
    if shared_pools is None:
        if mapping is None:
            from credinomina_reconciliation.complementary_distribution import shared_company_pools
            shared_pools = shared_company_pools()
        else:
            shared_pools = []
    mapping = load_payer_map() if mapping is None else mapping
    scope = {employer}
    while True:
        expanded = set(scope)
        for payer, beneficiaries in mapping.items():
            group = {payer, *beneficiaries}
            if scope & group:
                expanded.update(group)
        for group in shared_pools:
            if scope & set(group):
                expanded.update(group)
        if expanded == scope:
            return sorted(scope)
        scope = expanded


def permits_claim(deposit, claim, destination_group=None):
    if claim.get("manual_only"):
        groups = set(claim.get("groups") or [claim.get("group")]) - {None, ""}
        allowed = set(deposit.get("allowed_groups") or [deposit.get("group")]) - {None, ""}
        return (destination_group in groups & allowed if destination_group else len(groups & allowed) == 1)
    group = clean_text(claim.get("group"))
    payer = clean_text(deposit.get("group"))
    return not group or not payer or group in set(deposit.get("allowed_groups") or [payer])


def choose_detail_client(row, clients, payer, allowed=None, loan_clients=None):
    allowed = allowed_employers(payer) if allowed is None else set(allowed)
    company = clean_text(row.get("employer"))
    if company and company not in allowed:
        return None, "Conflicto: empresa no autorizada para este depósito"
    scoped = [client for client in clients if clean_text(client.get("employer")) in allowed
              and (not company or clean_text(client.get("employer")) == company)]
    from credinomina_reconciliation.deposit_identity import resolve_detail_identity
    return resolve_detail_identity(row, scoped, loan_clients)


def validate_paying_for(document):
    import frappe
    from frappe import _
    companies = [row.employer for row in document.get("paying_for") or []]
    if any(not company or company in {document.name, document.employer_name} for company in companies):
        frappe.throw(_("Seleccione otras empresas; la empresa pagadora siempre puede pagar por sí misma."))
    if len(companies) != len(set(companies)):
        frappe.throw(_("No repita empresas en Empresas por las que puede pagar."))
    previous = document.get_doc_before_save()
    removed = {row.employer for row in previous.get("paying_for") or []} - set(companies) if previous else set()
    if removed:
        # Do not silently invalidate confirmed allocations (including closed periods).
        for name in frappe.get_all("CN Remittance Allocation", filters={"employer": document.name, "docstatus": 1}, pluck="name"):
            if frappe.db.exists("CN Complementary Item", {"docstatus": 1, "category": "Saldo a favor del cliente",
                    "registered_deposit": name, "employer": ["in", sorted(removed)]}):
                frappe.throw(_("La autorización se utiliza en un saldo a favor del cliente del depósito {0}.").format(name))
            deposit = frappe.get_doc("CN Remittance Allocation", name)
            periods = set(selected_periods(deposit))
            for target in deposit.targets:
                if target.period:
                    periods.add(target.period)
                if target.historical_application:
                    periods.add(frappe.db.get_value("CN Source Row", target.historical_application, "historical_period"))
                if target.complementary_item and frappe.db.get_value("CN Complementary Item", target.complementary_item, "employer") in removed:
                    frappe.throw(_("La autorización se utiliza en el depósito confirmado {0}.").format(name))
            for entry in frappe.parse_json(deposit.allocation_detail or "[]"):
                if entry.get("periodo"):
                    periods.add(entry["periodo"])
                if entry.get("empresa") in removed:
                    frappe.throw(_("La autorización se utiliza en el depósito confirmado {0}.").format(name))
            if any(frappe.db.get_value("CN Reconciliation Period", period, "employer") in removed for period in periods if period):
                frappe.throw(_("La autorización se utiliza en el depósito confirmado {0}.").format(name))
