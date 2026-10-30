"""Read-only client resolution, with explicit confirmation of similar names."""
from difflib import SequenceMatcher

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


def _lookup(item):
    item.check_permission("read")
    probe = {field: clean_text(item.get(field)) for field in ("client_name", "client_number", "loan_number")}
    if probe["client_name"].casefold() in {"", "sin identificar", "0"}:
        probe["client_name"] = clean_text(item.get("source_client_name"))
    if probe["client_name"].casefold() in {"sin identificar", "0"}:
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
    return company, probe, candidates, loan_map, person, reason


def _suggestions(company, probe, candidates, loan_map):
    """Similarity proposes a consultation; it never proves financial identity."""
    query = name_key(probe["client_name"])
    if not company or not query:
        return []
    result = []
    for person in candidates:
        if person["employer"] != company:
            continue
        # A similar name cannot override contradictory numeric identifiers or
        # ambiguous loan ownership. Only the name may differ for this action.
        if probe["client_number"] and canonical_identifier(person["client_number"]) != canonical_identifier(probe["client_number"]):
            continue
        if loan_map is not None and loan_map.get(canonical_credit_number(probe["loan_number"])) != {person["name"]}:
            continue
        labels = [person["client_name"], *person["client_aliases"]]
        score = max(SequenceMatcher(None, query, name_key(label), autojunk=False).ratio() for label in labels)
        if score >= 0.65:
            result.append(dict(client=person["name"], client_name=person["client_name"],
                client_number=person["client_number"], employer=person["employer"], similarity=round(score * 100)))
    return sorted(result, key=lambda row: (-row["similarity"], row["client_name"], row["client"]))[:10]


def _select(lookup, selected_client=None):
    company, probe, candidates, loan_map, person, reason = lookup
    if selected_client:
        if not person or person["name"] != selected_client:
            suggested = {row["client"] for row in _suggestions(company, probe, candidates, loan_map)} if not person else set()
            if selected_client not in suggested:
                frappe.throw(_("El cliente seleccionado ya no corresponde a las sugerencias de esta partida. Consulte nuevamente."))
            person = next(row for row in candidates if row["name"] == selected_client)
    if not person:
        frappe.throw(_("No se pudo identificar un único cliente: {0}.").format(reason))
    from credinomina_reconciliation.accounting_related_records import _identity_values
    selected = frappe._dict(person, client=person["name"])
    # A manually chosen similar name is not a new registry alias. Keep the
    # consultation on the selected client's verified identifiers and aliases.
    verified_probe = dict(probe)
    if name_key(probe["client_name"]) not in {name_key(label) for label in [person["client_name"], *person["client_aliases"]]}:
        verified_probe["client_name"] = ""
    identity = _identity_values(selected, [selected, frappe._dict(verified_probe)])
    for alias in person["client_aliases"]:
        if clean_text(alias):
            identity["name_values"].add(clean_text(alias))
            identity["names"].add(name_key(alias))
    return company or person["employer"], identity


def resolve(item, selected_client=None):
    return _select(_lookup(item), selected_client)


@frappe.whitelist()
def get_related_pending(item_name, selected_client=None):
    from credinomina_reconciliation.reconciliation_workbench import _client_data

    item = frappe.get_doc("CN Complementary Item", item_name)
    lookup = _lookup(item)
    company, probe, candidates, loan_map, person, reason = lookup
    if not person and not selected_client:
        if company:
            frappe.get_doc("CN Employer", company).check_permission("read")
        return dict(needs_client_selection=True, candidates=_suggestions(company, probe, candidates, loan_map),
                    employer=company, query_name=probe["client_name"], reason=reason)
    company, identity = _select(lookup, selected_client)
    frappe.get_doc("CN Employer", company).check_permission("read")
    context = dict(complementary_item=item.name)
    if selected_client:
        context["client_choice"] = selected_client
    return dict(_client_data(company, identity), context=context)
