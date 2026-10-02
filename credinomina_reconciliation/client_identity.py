"""Deterministic client identity rules shared by the three file imports."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import canonical_identifier, clean_text


def name_key(value: Any) -> str:
    """Ignore accents, punctuation and first/last-name order, never spelling."""
    plain = unicodedata.normalize("NFKD", clean_text(value)).casefold()
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return " ".join(sorted(re.findall(r"[a-z0-9]+", plain)))


def matching_name(value: Any, candidate: Mapping[str, Any]) -> bool:
    key = name_key(value)
    return bool(key) and any(
        name_key(name) == key
        for name in (
            candidate.get("client_name"),
            *(candidate.get("client_aliases") or ()),
        )
    )


class ClientIdentityIndex:
    """Select a small candidate set without weakening the existing conflict rules."""

    def __init__(self, clients):
        self.records = list(clients)
        self.by_name = {row["name"]: row for row in self.records}
        self.identifiers = {field: defaultdict(set) for field in
                            ("client_number", "national_id", "employee_number")}
        self.names = defaultdict(set)
        for position, row in enumerate(self.records):
            for field, index in self.identifiers.items():
                key = canonical_identifier(row.get(field))
                if key:
                    index[key].add(position)
            for label in (row.get("client_name"), *(row.get("client_aliases") or ())):
                key = name_key(label)
                if key:
                    self.names[key].add(position)

    def __iter__(self):
        return iter(self.records)

    def candidates(self, record):
        positions = set()
        has_identifier = False
        for field, index in self.identifiers.items():
            key = canonical_identifier(record.get(field))
            if key:
                has_identifier = True
                positions.update(index.get(key, ()))
        if not has_identifier:
            positions.update(self.names.get(name_key(record.get("client_name")), ()))
        return [self.records[position] for position in sorted(positions)]


def choose_client(
    record: Mapping[str, Any], clients: Iterable[Mapping[str, Any]], employer: str = "",
) -> tuple[Mapping[str, Any] | None, str]:
    """Return an existing client or a reason to create/review; never fuzzy merge."""
    clients = clients.candidates(record) if isinstance(clients, ClientIdentityIndex) else list(clients)
    number = canonical_identifier(record.get("client_number"))
    national_id = canonical_identifier(record.get("national_id"))
    employee_number = canonical_identifier(record.get("employee_number"))
    employer = clean_text(employer or record.get("employer"))
    scoped = [row for row in clients if not employer or clean_text(row.get("employer")) == employer]
    if number or national_id or employee_number:
        by_number = [
            row for row in clients
            if number and canonical_identifier(row.get("client_number")) == number
        ]
        by_id = [
            row for row in clients
            if national_id and canonical_identifier(row.get("national_id")) == national_id
        ]
        by_employee = [
            row for row in scoped
            if employee_number and canonical_identifier(row.get("employee_number")) == employee_number
        ]
        combined = {row["name"]: row for row in by_number + by_id + by_employee}
        if len(combined) > 1:
            return None, "Conflicto: los identificadores pertenecen a clientes distintos"
        if len(combined) == 1:
            found = next(iter(combined.values()))
            if employer and clean_text(found.get("employer")) != employer:
                return None, "Conflicto: el identificador pertenece a otra empresa"
            if (
                number and found.get("client_number")
                and canonical_identifier(found["client_number"]) != number
            ) or (
                national_id and found.get("national_id")
                and canonical_identifier(found["national_id"]) != national_id
            ) or (
                employee_number and found.get("employee_number")
                and canonical_identifier(found["employee_number"]) != employee_number
            ):
                return None, "Conflicto de identificadores del cliente"
            return found, "Identificador exacto"
        return None, "Crear cliente"
    by_name = [row for row in scoped if matching_name(record.get("client_name"), row)]
    if len(by_name) == 1:
        return by_name[0], "Nombre o alias único"
    if len(by_name) > 1:
        return None, "Nombre ambiguo entre varios clientes"
    return None, "Crear cliente"
