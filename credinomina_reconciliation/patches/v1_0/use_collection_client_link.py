"""Resolve collection identities before retiring their duplicated customer number."""
from collections import defaultdict
from html import escape
import json

import frappe

from credinomina_reconciliation.client_identity import ClientIdentityIndex, choose_client
from credinomina_reconciliation.client_registry import load_client_index
from credinomina_reconciliation.collection_portfolio import enrich_collection_records
from credinomina_reconciliation.parsers import SourceFileError, canonical_identifier, clean_text


def plan_migration(rows, periods, clients):
    """Preflight all rows without writes; never merge or replace a conflicting link."""
    catalog = [dict(client) for client in clients]
    by_name = {client["name"]: client for client in catalog}
    lookup = ClientIdentityIndex(catalog)
    links, creations = [], []
    for row in rows:
        period = periods.get(row.get("parent"))
        if not period:
            raise ValueError(f"Fila {row['name']}: no existe el período {row.get('parent')}.")
        employer = period["employer"]
        linked = clean_text(row.get("client"))
        client = by_name.get(linked) if linked else None
        if linked and not client:
            raise ValueError(f"Período {row['parent']}, fila {row['name']}: el cliente vinculado {linked} no existe.")
        if client:
            if client.get("employer") != employer:
                raise ValueError(f"Período {row['parent']}, fila {row['name']}: el cliente {linked} pertenece a otra empresa.")
            for field in ("client_number", "national_id", "employee_number"):
                incoming, saved = canonical_identifier(row.get(field)), canonical_identifier(client.get(field))
                if incoming and saved and incoming != saved:
                    raise ValueError(f"Período {row['parent']}, fila {row['name']}: {field} contradice al cliente vinculado {linked}.")
        else:
            client, reason = choose_client(row, lookup, employer)
            if client is None:
                number, name = clean_text(row.get("client_number")), clean_text(row.get("client_name"))
                if reason != "Crear cliente" or not number or not name:
                    raise ValueError(f"Período {row['parent']}, fila {row['name']}: no se pudo identificar el cliente ({reason}). Complete la cartera o corrija la fila antes de migrar.")
                client = dict(name=number, client_number=number, client_name=name, employer=employer,
                              national_id=clean_text(row.get("national_id")),
                              employee_number=clean_text(row.get("employee_number")), client_aliases=[])
                if number in by_name:
                    raise ValueError(f"Fila {row['name']}: el número {number} ya identifica otro cliente.")
                creations.append(client)
                catalog.append(client)
                by_name[number] = client
                lookup = ClientIdentityIndex(catalog)
        links.append(dict(row=row["name"], period=row["parent"], client=client["name"],
                          previous_client=row.get("client") or "", previous_number=row.get("client_number") or ""))
    return links, creations


def execute():
    if not frappe.db.table_exists("CN Collection Row"):
        return
    legacy = frappe.db.has_column("CN Collection Row", "client_number")
    rows = frappe.get_all("CN Collection Row", fields=[
        "name", "parent", "client", "client_name", "national_id", "employee_number", "loan_number", "source_row",
        *(["client_number"] if legacy else []),
    ], limit_page_length=0)
    if not rows or all(row.client and not row.get("client_number") for row in rows):
        return
    periods = {row.name: row for row in frappe.get_all("CN Reconciliation Period",
        filters={"name": ["in", sorted({row.parent for row in rows})]},
        fields=["name", "employer", "cutoff_date"], limit_page_length=0)}
    clients = load_client_index()
    lookup = ClientIdentityIndex(clients)
    # Only identities unresolved by the registry need portfolio evidence.
    incomplete = defaultdict(list)
    originals = {row.name: dict(row) for row in rows}
    for row in rows:
        if not row.client and not row.get("client_number") and row.parent in periods:
            found, reason = choose_client(row, lookup, periods[row.parent].employer)
            if not found and reason == "Crear cliente":
                incomplete[row.parent].append(row)
    try:
        for parent, records in incomplete.items():
            enrich_collection_records(records, periods[parent].employer, periods[parent].cutoff_date)
        links, creations = plan_migration(rows, periods, clients)
    except (ValueError, SourceFileError) as exc:
        frappe.throw(str(exc), title="Revisar clientes de cobranza antes de migrar")

    for client in creations:
        frappe.get_doc({"doctype": "CN Client", **{key: value for key, value in client.items()
            if key not in {"name", "client_aliases"}}}).insert(ignore_permissions=True)
    audit = defaultdict(list)
    for link in links:
        original = originals[link["row"]]
        if original.get("client") == link["client"] and not original.get("client_number"):
            continue
        changes = {"client": link["client"]}
        if legacy:
            changes["client_number"] = None
        frappe.db.set_value("CN Collection Row", link["row"], changes, update_modified=False)
        audit[link["period"]].append(dict(fila=link["row"], cliente_anterior=original.get("client"),
            numero_original=original.get("client_number"), cliente=link["client"]))
    for parent, evidence in audit.items():
        # Preserve imported spelling/zero padding in an audit record, not a second identifier.
        frappe.get_doc(dict(doctype="Comment", comment_type="Info",
            reference_doctype="CN Reconciliation Period", reference_name=parent,
            content="Cliente único en cobranza. Se conservaron filas, importes y vínculos.<pre>"
                    + escape(json.dumps(evidence, ensure_ascii=False)) + "</pre>")).insert(ignore_permissions=True)
