"""Resolve a readable complementary item's client without changing its identity."""
import frappe
from frappe import _

from credinomina_reconciliation.client_identity import ClientIdentityIndex, choose_client, name_key
from credinomina_reconciliation.credit_lookup import get_credit_rows
from credinomina_reconciliation.deposit_identity import resolve_detail_identity
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.report_records import child_records, records


def _loan_owners(loan, company, clients):
    index = ClientIdentityIndex(clients)
    by_id = {row["name"]: row for row in clients}
    owners = set()

    def add(row, link_field, number_field):
        number = row.get(number_field)
        person, _reason = choose_client(dict(client_number=number, national_id=row.get("national_id"),
                                             client_name=row.get("client_name")), index)
        linked = by_id.get(row.get(link_field)) if row.get(link_field) else None
        if row.get(link_field):
            if not linked or (person and linked["name"] != person["name"]) or any(
                row.get(field) and linked.get(target) and canonical_identifier(row[field]) != canonical_identifier(linked[target])
                for field, target in ((number_field, "client_number"), ("national_id", "national_id"))
            ):
                owners.add(None)
                return
            person = linked
        owners.add(person["name"] if person else None)

    if frappe.has_permission("CN Accounting Import", "read"):
        imports = list(records("CN Accounting Import", filters={"docstatus": ["!=", 2],
            "status": ["in", ["Importado", "Importado con excepciones"]],
            **({"employer": company} if company else {})}, fields=["name"]))
        for row in get_credit_rows("CN Source Row", filters={
                "parent": ["in", [item.name for item in imports]], "parenttype": "CN Accounting Import",
                "parentfield": "rows", "event_type": "Aplicacion", "effective": 1, "docstatus": ["!=", 2]},
                fields=["name", "loan_number", "client", "client_number", "national_id", "client_name"],
                loan_field="loan_number", loans=[loan]):
            add(row, "client", "client_number")
    if frappe.has_permission("CN Credit Portfolio Snapshot", "read"):
        snapshots = list(records("CN Credit Portfolio Snapshot", filters={"disabled": 0,
            "status": ["in", ["Importado", "Importado con alertas"]]}, fields=["name"]))
        for row in get_credit_rows("CN Credit Portfolio Row", filters={
                "parent": ["in", [item.name for item in snapshots]], "parenttype": "CN Credit Portfolio Snapshot",
                **({"employer": company} if company else {})},
                fields=["name", "credit_number", "matched_client", "client_number_core", "national_id", "client_name"],
                loan_field="credit_number", loans=[loan]):
            add(row, "matched_client", "client_number_core")
    return owners


def resolve(item):
    from credinomina_reconciliation.accounting_related_records import _identity_values

    item.check_permission("read")
    probe = {field: clean_text(item.get(field)) for field in ("client_name", "client_number", "loan_number")}
    if probe["client_name"].casefold() == "sin identificar":
        probe["client_name"] = ""
    if not any(probe.values()):
        frappe.throw(_("Indique nombre, número de cliente o crédito para consultar las partidas vinculadas."))
    frappe.has_permission("CN Client", "read", throw=True)
    clients = [dict(row, client_aliases=[]) for row in records("CN Client",
        fields=["name", "employer", "client_name", "client_number", "national_id"])]
    by_id = {row["name"]: row for row in clients}
    for alias in child_records("CN Client Alias", by_id, "CN Client", "aliases", fields=["parent", "alias_name"]):
        by_id[alias.parent]["client_aliases"].append(alias.alias_name)
    company = clean_text(item.get("employer"))
    owners = _loan_owners(probe["loan_number"], company, clients) if probe["loan_number"] else set()
    candidates = [row for row in clients if not company or row["employer"] == company
                  or row["name"] in owners or (probe["client_number"] and
                      canonical_identifier(row["client_number"]) == canonical_identifier(probe["client_number"]))]
    loan_map = {canonical_credit_number(probe["loan_number"]): owners} if probe["loan_number"] else None
    person, reason = resolve_detail_identity(probe, candidates, loan_map)
    if not person:
        frappe.throw(_("No se pudo identificar un único cliente: {0}.").format(reason))
    selected = frappe._dict(person, client=person["name"])
    identity = _identity_values(selected, [selected, frappe._dict(probe)])
    for alias in person["client_aliases"]:
        if clean_text(alias):
            identity["name_values"].add(clean_text(alias))
            identity["names"].add(name_key(alias))
    return company or person["employer"], identity
