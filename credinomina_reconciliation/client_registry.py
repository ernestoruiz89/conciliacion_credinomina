"""Frappe-backed client catalog shared by collection and portfolio imports."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.client_identity import (
    ClientIdentityIndex,
    choose_client,
    matching_name,
    name_key,
)
from credinomina_reconciliation.employer_naming import (
    AccountingEmployerResolver,
    attach_employer_aliases,
)
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def load_client_index(employers=None):
    if employers is not None and not employers:
        return []
    rows = frappe.get_all(
        "CN Client",
        filters={"employer": ["in", sorted(set(employers))]} if employers is not None else {},
        fields=["name", "employer", "client_name", "client_number", "employee_number", "national_id"],
        limit_page_length=100000,
    )
    aliases = defaultdict(list)
    for row in frappe.get_all(
        "CN Client Alias", fields=["parent", "alias_name"],
        filters={"parent": ["in", [row.name for row in rows]]} if employers is not None else {},
        limit_page_length=100000,
    ) if rows else []:
        aliases[row.parent].append(row.alias_name)
    return [
        {**row, "client_aliases": aliases[row.name]}
        for row in rows
    ]


class ClientIndex:
    def __init__(self):
        self.records = load_client_index()

    def ensure_from_collection(self, record, employer):
        client, reason = choose_client(record, self.records, employer)
        if reason.startswith("Conflicto") or reason.startswith("Nombre ambiguo"):
            frappe.throw(_("Fila {0}: {1}.").format(record.get("source_row"), reason))
        if client is None:
            if not clean_text(record.get("client_number")):
                return ""  # Keep the collection row; never invent a core client number.
            document = frappe.get_doc({
                "doctype": "CN Client",
                "employer": employer,
                "client_name": clean_text(record.get("client_name")),
                "client_number": clean_text(record.get("client_number")),
                "employee_number": clean_text(record.get("employee_number")),
                "national_id": clean_text(record.get("national_id")),
            })
            document.insert(ignore_permissions=True)
            self.records.append({
                "name": document.name,
                "employer": document.employer,
                "client_name": document.client_name,
                "client_number": document.client_number,
                "employee_number": document.employee_number,
                "national_id": document.national_id,
                "client_aliases": [],
            })
            return document.name
        if reason == "Identificador exacto":
            document = frappe.get_doc("CN Client", client["name"])
            changed = False
            for fieldname in ("client_number", "employee_number", "national_id"):
                if not document.get(fieldname) and record.get(fieldname):
                    document.set(fieldname, clean_text(record[fieldname]))
                    client[fieldname] = document.get(fieldname)
                    changed = True
            incoming_name = clean_text(record.get("client_name"))
            known = [document.client_name] + [row.alias_name for row in document.aliases]
            if name_key(incoming_name) not in {name_key(value) for value in known}:
                document.append("aliases", {"alias_name": incoming_name})
                client["client_aliases"].append(incoming_name)
                changed = True
            if changed:
                document.save(ignore_permissions=True)
        return client["name"]

    def ensure_from_source_import(self, record, employer):
        """Resolve or create a client from an accounting movement without blocking import."""
        employer = clean_text(employer)
        if not employer or not frappe.db.exists("CN Employer", employer):
            return "", "No creado: empresa no identificada"

        candidate = {
            "employer": employer,
            "client_name": clean_text(record.get("client_name")),
            "client_number": clean_text(record.get("client_number")),
            "employee_number": clean_text(record.get("employee_number")),
            "national_id": clean_text(record.get("national_id")),
        }

        # A portfolio link was already identity-checked against the credit cut.
        # Keep that verified link even when the movement contains a different
        # client-number convention (for example core number vs. SIAF number).
        portfolio_link = clean_text(record.get("portfolio_client"))
        if portfolio_link:
            linked, status = self.ensure_from_portfolio(
                candidate | {"portfolio_client": portfolio_link}, employer
            )
            if linked:
                return linked, status
            return "", status

        existing_link = clean_text(record.get("client"))
        if existing_link:
            linked_client = next(
                (row for row in self.records if row.get("name") == existing_link),
                None,
            )
            if not linked_client:
                return "", "Revisar: el cliente relacionado ya no existe"
            if clean_text(linked_client.get("employer")) != employer:
                return "", "Revisar: el cliente relacionado pertenece a otra empresa"
            for fieldname in ("client_number", "employee_number", "national_id"):
                incoming_id = canonical_identifier(candidate.get(fieldname))
                linked_id = canonical_identifier(linked_client.get(fieldname))
                if incoming_id and linked_id and incoming_id != linked_id:
                    return "", "Revisar: {0} no coincide con el cliente relacionado".format(
                        fieldname.replace("_", " ")
                    )
            self._complete_existing_client(linked_client, candidate)
            return existing_link, "Cliente existente"

        client, reason = choose_client(candidate, self.records, employer)
        if reason.startswith("Conflicto") or "ambiguo" in reason.casefold():
            return "", "Revisar: {0}".format(reason)
        if client is None and reason == "Crear cliente":
            named = [
                row for row in self.records
                if clean_text(row.get("employer")) == employer
                and matching_name(candidate["client_name"], row)
            ]
            if len(named) > 1:
                return "", "Revisar: nombre ambiguo entre varios clientes"
            if len(named) == 1:
                possible = named[0]
                conflicts = [
                    fieldname for fieldname in (
                        "client_number", "employee_number", "national_id",
                    )
                    if candidate.get(fieldname)
                    and possible.get(fieldname)
                    and canonical_identifier(candidate[fieldname])
                    != canonical_identifier(possible[fieldname])
                ]
                if conflicts:
                    return "", "Revisar: el nombre coincide, pero sus identificadores no"
                self._complete_existing_client(possible, candidate)
                return possible["name"], "Cliente existente"
        if client:
            if reason == "Identificador exacto":
                self._complete_existing_client(client, candidate)
            return client["name"], "Cliente existente"

        if not candidate["client_name"]:
            return "", "No creado: falta nombre de cliente"
        if not candidate["client_number"]:
            return "", "No creado: falta número de cliente"

        document = frappe.get_doc({
            "doctype": "CN Client",
            "employer": employer,
            "client_name": candidate["client_name"],
            "client_number": candidate["client_number"],
            "employee_number": candidate["employee_number"],
            "national_id": candidate["national_id"],
        })
        document.insert(ignore_permissions=True)
        self.records.append({
            "name": document.name,
            **candidate,
            "client_aliases": [],
        })
        return document.name, "Cliente creado desde movimiento contable"

    @staticmethod
    def _complete_existing_client(client, candidate):
        """Fill blank identifiers and keep the imported name as a verified alias."""
        document = frappe.get_doc("CN Client", client["name"])
        changed = False
        for fieldname in ("client_number", "employee_number", "national_id"):
            value = clean_text(candidate.get(fieldname))
            if not document.get(fieldname) and value:
                document.set(fieldname, value)
                client[fieldname] = value
                changed = True

        incoming_name = clean_text(candidate.get("client_name"))
        if incoming_name:
            known_names = [document.get("client_name")]
            known_names.extend(
                row.get("alias_name") if hasattr(row, "get") else row
                for row in (document.get("aliases") or [])
            )
            if name_key(incoming_name) not in {
                name_key(value) for value in known_names if value
            }:
                document.append("aliases", {"alias_name": incoming_name})
                client.setdefault("client_aliases", []).append(incoming_name)
                changed = True
        if changed:
            document.save(ignore_permissions=True)

    def ensure_from_portfolio(self, record, employer):
        """Link or create a client only from a validated portfolio employer."""
        employer = clean_text(employer)
        if not employer or not frappe.db.exists("CN Employer", employer):
            return "", "No creado: empresa de cartera no validada"

        candidate = {
            "employer": employer,
            "client_name": clean_text(record.get("client_name")),
            "client_number": clean_text(record.get("client_number")),
            "national_id": clean_text(record.get("national_id")),
            "employee_number": "",
        }

        portfolio_client_name = clean_text(record.get("portfolio_client"))
        linked_client = next(
            (row for row in self.records if row["name"] == portfolio_client_name),
            None,
        ) if portfolio_client_name else None
        if linked_client:
            if clean_text(linked_client.get("employer")) != employer:
                return "", "Revisar: cliente relacionado pertenece a otra empresa"
            if (
                candidate["national_id"] and linked_client.get("national_id")
                and canonical_identifier(candidate["national_id"])
                != canonical_identifier(linked_client["national_id"])
            ):
                return "", "Revisar: la cédula de cartera no coincide con el cliente relacionado"
            document = None
            changed = False
            for fieldname in ("client_number", "national_id"):
                if candidate[fieldname] and not linked_client.get(fieldname):
                    if document is None:
                        document = frappe.get_doc("CN Client", linked_client["name"])
                    document.set(fieldname, candidate[fieldname])
                    linked_client[fieldname] = candidate[fieldname]
                    changed = True
            if changed:
                document.save(ignore_permissions=True)
            return linked_client["name"], "Cliente existente"

        if not candidate["client_name"]:
            return "", "No creado: falta nombre de cliente en cartera"

        client, reason = choose_client(candidate, self.records, employer)
        if reason.startswith("Conflicto") or "ambiguo" in reason.casefold():
            return "", "Revisar: {0}".format(reason)

        if client:
            if reason == "Identificador exacto":
                document = frappe.get_doc("CN Client", client["name"])
                changed = False
                for fieldname in ("client_number", "national_id"):
                    if not document.get(fieldname) and candidate.get(fieldname):
                        document.set(fieldname, candidate[fieldname])
                        client[fieldname] = candidate[fieldname]
                        changed = True
                if changed:
                    document.save(ignore_permissions=True)
            return client["name"], "Cliente existente"

        if not candidate["client_number"]:
            return "", "No creado: falta número de cliente en cartera"

        document = frappe.get_doc({
            "doctype": "CN Client",
            "employer": employer,
            "client_name": candidate["client_name"],
            "client_number": candidate["client_number"],
            "national_id": candidate["national_id"],
        })
        document.insert(ignore_permissions=True)
        self.records.append({
            "name": document.name,
            **candidate,
            "client_aliases": [],
        })
        return document.name, "Cliente creado automáticamente desde cartera"


def names_for_claim(claim, clients, employer=""):
    """Return verified names for a claim, or its own source name if unknown."""
    names = [clean_text(claim.get("client_name"))]
    linked_name = claim.get("client") or claim.get("portfolio_client")
    found = clients.by_name.get(linked_name) if isinstance(clients, ClientIdentityIndex) else next(
        (row for row in clients if linked_name and row.get("name") == linked_name),
        None,
    )
    if found and employer and clean_text(found.get("employer")) != clean_text(employer):
        found = None
    if found:
        names.extend([found["client_name"], *(found.get("client_aliases") or ())])
    else:
        found, reason = choose_client(claim, clients, employer)
        if found and reason in {"Identificador exacto", "Nombre o alias único"}:
            names.extend([found["client_name"], *(found.get("client_aliases") or ())])
    return [name for name in dict.fromkeys(names) if name]


def enrich_source_import_clients(records, client_index=None):
    """Attach CN Client links to accounting application rows, creating safely."""
    client_index = client_index or ClientIndex()
    employers = frappe.get_all(
        "CN Employer",
        fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    attach_employer_aliases(employers)
    employer_resolver = AccountingEmployerResolver(employers)
    employer_names = {clean_text(row.get("name")) for row in employers}

    for record in records:
        if record.get("event_type") != "Aplicacion":
            continue

        employer = clean_text(record.get("portfolio_employer") or record.get("_manual_employer"))
        if employer not in employer_names:
            linked_name = clean_text(record.get("portfolio_client") or record.get("client"))
            linked_client = next(
                (row for row in client_index.records if row.get("name") == linked_name),
                None,
            ) if linked_name else None
            if linked_client:
                employer = clean_text(linked_client.get("employer"))

        employer_text = clean_text(record.get("employer_text"))
        if employer not in employer_names and employer_text:
            employer, issue = employer_resolver.resolve({"employer_text": employer_text})
            if issue == "Empresa o alias ambiguo":
                record["client"] = ""
                record["client_registry_status"] = "No creado: empresa ambigua"
                continue

        client_name, status = client_index.ensure_from_source_import(record, employer)
        record["client"] = client_name
        record["client_registry_status"] = status
    return records
