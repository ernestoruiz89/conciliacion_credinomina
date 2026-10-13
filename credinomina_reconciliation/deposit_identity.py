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


class DetailIdentityEvidence(dict):
    """Loan ownership plus verified company profiles; never change the registry."""
    def __init__(self):
        super().__init__()
        self.company_clients = defaultdict(dict)
        self.loan_employers = defaultdict(set)
        self.current_index = None
        self.historical_index = None


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
                     any(canonical_identifier(value) == canonical_identifier(identifier) for identifier in
                         ([client.get(field), *(client.get('employee_numbers') or [])] if field == 'employee_number' else [client.get(field)])))}
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
    if not clients:
        return DetailIdentityEvidence()
    result = defaultdict(set)
    evidence_index = DetailIdentityEvidence()
    by_name = {client["name"]: client for client in clients}
    # Client number / national ID are stable person identifiers. Current company
    # and current employee number are NOT historical company membership proof.
    global_index = ClientIdentityIndex(clients)
    evidence_index.current_index = global_index

    def add(record, loan, link, number):
        company = record.get("employer")
        evidence_index.loan_employers[credit_key(loan)].add(company)
        evidence = {"client_number": number, "national_id": record.get("national_id"),
                    "client_name": record.get("client_name")}
        client, _reason = choose_client(evidence, global_index)
        if link:
            linked = by_name.get(link)
            if not linked or (client and client["name"] != link):
                result[credit_key(loan)].add(None)
                return
            if any(evidence.get(field) and linked.get(field) and
                   canonical_identifier(evidence[field]) != canonical_identifier(linked[field])
                   for field in ("client_number", "national_id")):
                result[credit_key(loan)].add(None)
                return
            client = linked
        if client and company in allowed:
            profile = dict(client, employer=company,
                           employee_number=record.get('employee_number') or (client.get('employee_number') if client.get('employer') == company else ''))
            profile['employee_numbers'] = [value for value in [record.get('employee_number'),
                client.get('employee_number') if client.get('employer') == company else ''] if value]
            # Keep names reported by this company as historical identity aliases.
            profile['client_aliases'] = list(dict.fromkeys([*(client.get('client_aliases') or []), record.get('client_name') or '']))
            previous = evidence_index.company_clients[company].get(client['name'])
            if previous:
                profile['client_aliases'] = list(dict.fromkeys([*previous.get('client_aliases', []), *profile['client_aliases']]))
                profile['employee_numbers'] = list(dict.fromkeys([*previous.get('employee_numbers', []), *profile['employee_numbers']]))
            evidence_index.company_clients[company][client['name']] = profile
        result[credit_key(loan)].add(client["name"] if client else None)

    # Without a credit, find the person first, then require actual historical
    # evidence in an authorized company. A same name alone never authorizes it.
    requested_clients = {client['name'] for row in rows for client in global_index.candidates(
        {field: row.get(field) for field in ('client_number', 'national_id', 'client_name')})}

    def evidence_rows(doctype, filters, fields, loan_field):
        seen = set()
        for record in get_credit_rows(doctype, filters=filters, fields=fields, loan_field=loan_field, loans=loans) if loans else []:
            seen.add(record.get('name'))
            yield record
        link_field = 'matched_client' if doctype == 'CN Credit Portfolio Row' else 'client'
        number_field = 'client_number_core' if doctype == 'CN Credit Portfolio Row' else 'client_number'
        # Old rows may lack the Link; stable core numbers still prove identity.
        numbers = [client.get('client_number') for client in clients if client['name'] in requested_clients and client.get('client_number')]
        employees = sorted({value for row in rows for value in (clean_text(row.get('employee_number')), canonical_identifier(row.get('employee_number'))) if value}) if doctype == 'CN Source Row' else []
        for offset in range(0, max(len(requested_clients), len(numbers), len(employees)), 500):
            identifiers = []
            ids = sorted(requested_clients)[offset:offset + 500]
            if ids:
                identifiers.append([link_field, 'in', ids])
            if numbers[offset:offset + 500]:
                identifiers.append([number_field, 'in', numbers[offset:offset + 500]])
            if employees[offset:offset + 500]:
                identifiers.append(['employee_number', 'in', employees[offset:offset + 500]])
            if not identifiers:
                continue
            for record in frappe.get_all(doctype, filters=filters, or_filters=identifiers, fields=fields, limit_page_length=0):
                if record.get('name') not in seen:
                    seen.add(record.get('name'))
                    yield record

    snapshots = frappe.get_all("CN Credit Portfolio Snapshot",
        filters={"disabled": 0, "status": ["in", ["Importado", "Importado con alertas"]]}, pluck="name", limit_page_length=0)
    if snapshots:
        for row in evidence_rows("CN Credit Portfolio Row", filters={
                "parent": ["in", snapshots], "parenttype": "CN Credit Portfolio Snapshot",
                "employer": ["in", sorted(allowed)]},
                fields=["name", "credit_number", "matched_client", "client_number_core", "national_id", "client_name", "employer"],
                loan_field="credit_number"):
            add(row, row.get("credit_number"), row.get("matched_client"), row.get("client_number_core"))
    imports = frappe.get_all("CN Accounting Import", filters={"employer": ["in", sorted(allowed)],
        "docstatus": ["!=", 2], "status": ["in", ["Importado", "Importado con excepciones"]]},
        fields=["name", "employer"], limit_page_length=0)
    companies = {item["name"]: item["employer"] for item in imports}
    if companies:
        for row in evidence_rows("CN Source Row", filters={
                "parenttype": "CN Accounting Import", "parent": ["in", list(companies)],
                "docstatus": ["!=", 2], "event_type": "Aplicacion", "effective": 1},
                fields=["name", "parent", "loan_number", "client", "client_number", "national_id", "client_name", "employee_number"],
                loan_field="loan_number"):
            add({**row, "employer": companies[row["parent"]]}, row.get("loan_number"),
                row.get("client"), row.get("client_number"))
    evidence_index.update(result)
    evidence_index.historical_index = ClientIdentityIndex(
        [profile for profiles in evidence_index.company_clients.values() for profile in profiles.values()])
    return evidence_index
