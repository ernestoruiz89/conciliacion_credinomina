"""Frappe-backed client catalog shared by collection and portfolio imports."""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.client_identity import choose_client, name_key
from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def load_client_index():
    rows = frappe.get_all(
        "CN Client",
        fields=["name", "employer", "client_name", "client_number", "employee_number", "national_id"],
        limit_page_length=100000,
    )
    aliases = defaultdict(list)
    for row in frappe.get_all(
        "CN Client Alias", fields=["parent", "alias_name"],
        limit_page_length=100000,
    ):
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
    found, reason = choose_client(claim, clients, employer)
    names = [clean_text(claim.get("client_name"))]
    if found and reason in {"Identificador exacto", "Nombre o alias único"}:
        names.extend([found["client_name"], *(found.get("client_aliases") or ())])
    return [name for name in dict.fromkeys(names) if name]
