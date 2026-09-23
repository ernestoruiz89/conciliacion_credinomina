"""Deterministic client identity rules shared by the three file imports."""

from __future__ import annotations

import re
import unicodedata
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


def choose_client(
    record: Mapping[str, Any], clients: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str]:
    """Return an existing client or a reason to create/review; never fuzzy merge."""
    clients = list(clients)
    number = canonical_identifier(record.get("client_number"))
    national_id = canonical_identifier(record.get("national_id"))
    if number or national_id:
        by_number = [
            row for row in clients
            if number and canonical_identifier(row.get("client_number")) == number
        ]
        by_id = [
            row for row in clients
            if national_id and canonical_identifier(row.get("national_id")) == national_id
        ]
        combined = {row["name"]: row for row in by_number + by_id}
        if len(combined) > 1:
            return None, "Conflicto: número de cliente y cédula pertenecen a clientes distintos"
        if len(combined) == 1:
            found = next(iter(combined.values()))
            if (
                number and found.get("client_number")
                and canonical_identifier(found["client_number"]) != number
            ) or (
                national_id and found.get("national_id")
                and canonical_identifier(found["national_id"]) != national_id
            ):
                return None, "Conflicto de identificadores del cliente"
            return found, "Identificador exacto"
        return None, "Crear cliente"
    by_name = [row for row in clients if matching_name(record.get("client_name"), row)]
    if len(by_name) == 1:
        return by_name[0], "Nombre o alias único"
    if len(by_name) > 1:
        return None, "Nombre ambiguo entre varios clientes"
    return None, "Crear cliente"
