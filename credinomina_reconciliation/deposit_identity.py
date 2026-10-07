"""Strict, company-scoped identity evidence for deposit details."""
from collections import defaultdict

from credinomina_reconciliation.client_identity import ClientIdentityIndex, choose_client, matching_name
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.credit_lookup import get_credit_rows


IDENTIFIERS = {
    "client_number": "Nro. Cliente",
    "national_id": "Nro. Cédula",
    "employee_number": "Nro. Empleado",
    "client_name": "Nombre y apellidos",
}


def credit_key(value):
    return canonical_credit_number(value)


def resolve_detail_identity(row, clients, loan_clients=None):
    """Every supplied identifier must agree; never discard contradictory evidence."""
    candidates = {client["name"]: client for client in clients}
    matches = []
    for field, label in IDENTIFIERS.items():
        value = clean_text(row.get(field))
        if not value:
            continue
        found = {key for key, client in candidates.items()
                 if (matching_name(value, client) if field == "client_name" else
                     canonical_identifier(value) == canonical_identifier(client.get(field)))}
        if not found:
            return None, f"Conflicto: no se pudo verificar {label} en los clientes de la empresa"
        matches.append(found)
    if row.get("loan_number") and loan_clients is not None:
        found = set(loan_clients.get(credit_key(row.get("loan_number")), ()))
        if not found or None in found:
            return None, "Conflicto: no se pudo verificar el cliente del crédito en cartera o aplicaciones"
        # A loan belonging to different people must never be disambiguated by a name.
        if len(found) != 1:
            return None, "Conflicto: el crédito corresponde a varios clientes"
        matches.append(found & set(candidates))
    if not matches:
        return None, "Conflicto: no se pudo identificar al cliente"
    found = set.intersection(*matches)
    if not found:
        return None, "Conflicto: los datos informados no corresponden al mismo cliente"
    if len(found) > 1:
        return None, "Nombre ambiguo entre varios clientes" if len(matches) == 1 and row.get("client_name") else "Conflicto: identidad ambigua entre varios clientes"
    return candidates[found.pop()], "Identificadores coincidentes" if len(matches) > 1 else (
        "Nombre o alias único" if row.get("client_name") else "Identificador exacto")


def load_detail_loan_clients(rows, clients, allowed):
    """Batch-load only the requested loans; unknown/conflicting evidence blocks matching."""
    import frappe
    loans = {credit_key(row.get("loan_number")) for row in rows if row.get("loan_number")}
    if not loans or not clients:
        return {}
    result = defaultdict(set)
    by_name = {client["name"]: client for client in clients}
    company_clients = defaultdict(list)
    for client in clients:
        company_clients[client.get("employer")].append(client)
    indexes = {company: ClientIdentityIndex(records) for company, records in company_clients.items()}

    def add(record, loan, link, number):
        company = record.get("employer")
        evidence = {"client_number": number, "national_id": record.get("national_id"),
                    "client_name": record.get("client_name")}
        client, _reason = choose_client(evidence, indexes.get(company, []), company)
        if link:
            linked = by_name.get(link)
            if not linked or linked.get("employer") != company or (client and client["name"] != link):
                result[credit_key(loan)].add(None)
                return
            if any(evidence.get(field) and linked.get(field) and
                   canonical_identifier(evidence[field]) != canonical_identifier(linked[field])
                   for field in ("client_number", "national_id")):
                result[credit_key(loan)].add(None)
                return
            client = linked
        result[credit_key(loan)].add(client["name"] if client else None)

    snapshots = frappe.get_all("CN Credit Portfolio Snapshot",
        filters={"disabled": 0, "status": ["in", ["Importado", "Importado con alertas"]]}, pluck="name", limit_page_length=0)
    if snapshots:
        for row in get_credit_rows("CN Credit Portfolio Row", filters={
                "parent": ["in", snapshots], "parenttype": "CN Credit Portfolio Snapshot",
                "employer": ["in", sorted(allowed)]},
                fields=["credit_number", "matched_client", "client_number_core", "national_id", "client_name", "employer"],
                loan_field="credit_number", loans=loans):
            add(row, row.get("credit_number"), row.get("matched_client"), row.get("client_number_core"))
    imports = frappe.get_all("CN Accounting Import", filters={"employer": ["in", sorted(allowed)],
        "docstatus": ["!=", 2]}, fields=["name", "employer"], limit_page_length=0)
    companies = {item["name"]: item["employer"] for item in imports}
    if companies:
        for row in get_credit_rows("CN Source Row", filters={
                "parenttype": "CN Accounting Import", "parent": ["in", list(companies)],
                "docstatus": ["!=", 2], "event_type": "Aplicacion"},
                fields=["parent", "loan_number", "client", "client_number", "national_id", "client_name"],
                loan_field="loan_number", loans=loans):
            add({**row, "employer": companies[row["parent"]]}, row.get("loan_number"),
                row.get("client"), row.get("client_number"))
    return dict(result)
