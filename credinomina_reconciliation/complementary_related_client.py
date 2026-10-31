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


def _completion_details(item, client=None):
    fields = [field for field in ("client_name", "client_number") if not clean_text(item.get(field))]
    available = bool(fields and item.docstatus in (0, 1) and not item.get("generic_distribution")
        and item.get("category") not in {"Saldo a favor de la empresa", "Diferencia por tolerancia", "Vinculado a depósito"}
        and not item.get("origin_deposit") and not item.get("movement_key")
        and frappe.has_permission("CN Complementary Item", "write", doc=item)
        and (item.docstatus == 0 or frappe.has_permission("CN Complementary Item", "submit", doc=item)))
    return dict(available=available, fields=fields, modified=str(item.modified), client=client)


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
                    employer=company, query_name=probe["client_name"], reason=reason,
                    client_completion=_completion_details(item))
    company, identity = _select(lookup, selected_client)
    frappe.get_doc("CN Employer", company).check_permission("read")
    context = dict(complementary_item=item.name)
    if selected_client:
        context["client_choice"] = selected_client
    return dict(_client_data(company, identity), context=context,
                client_completion=_completion_details(item, next(iter(identity["clients"]))))


def _guard_completion(item, changes, selected_client):
    """Keep the existing protections for confirmed financial identities."""
    from credinomina_reconciliation.reclassification_distribution import guard_journal
    from credinomina_reconciliation.complementary_compensation import guard_document
    from credinomina_reconciliation.manual_origin_link import guard_item
    from credinomina_reconciliation.receivable_recovery import guard_receipt
    from credinomina_reconciliation.client_credit import _assert_financial_identity, CREDIT_CATEGORIES

    if item.get("credit_client") and item.credit_client != selected_client:
        frappe.throw(_("El cliente elegido no coincide con el beneficiario registrado en la partida."))
    candidate = frappe.get_doc(item.as_dict())
    candidate.update(changes)
    guard_journal(candidate, item)
    guard_document(candidate, item)
    guard_item(candidate, item)
    guard_receipt(candidate, item)
    if item.docstatus == 1 and item.category in CREDIT_CATEGORIES:
        _assert_financial_identity(candidate, item)


@frappe.whitelist(methods=["POST"])
def complete_client_fields(item_name, selected_client, modified):
    from frappe.utils import escape_html

    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    if item.docstatus == 1:
        item.check_permission("submit")
    if str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Actualice la consulta antes de completar los datos."))
    details = _completion_details(item)
    if not details["available"]:
        frappe.throw(_("La partida no tiene datos vacíos que pueda completar con este tratamiento y sus permisos actuales."))
    if not clean_text(selected_client):
        frappe.throw(_("Seleccione el cliente antes de completar los datos."))
    _company, identity = resolve(item, selected_client)
    client = frappe.get_doc("CN Client", selected_client, for_update=True)
    client.check_permission("read")
    if client.client_name != identity["client_name"] or canonical_identifier(client.client_number) not in identity["numbers"]:
        frappe.throw(_("Los datos del cliente cambiaron. Consulte nuevamente las sugerencias."))
    changes = {field: client.get(field) for field in details["fields"] if clean_text(client.get(field))}
    if not changes:
        frappe.throw(_("El cliente elegido no tiene datos para completar los campos vacíos."))
    _guard_completion(item, changes, selected_client)
    # Exact whitelist and blank-only updates: a normal save would reclassify
    # imported items and recalculate balances unrelated to this enrichment.
    frappe.db.set_value("CN Complementary Item", item.name, changes)
    item.add_comment("Comment", _("Datos vacíos completados con el cliente {0}: {1}.").format(
        escape_html(client.name), escape_html(frappe.as_json(changes))))
    frappe.clear_document_cache("CN Complementary Item", item.name)
    return dict(name=item.name, updated_fields=changes)
