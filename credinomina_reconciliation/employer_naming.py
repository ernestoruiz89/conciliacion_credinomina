"""Plan safe CN Employer renames without depending on Frappe at import time."""

from __future__ import annotations

from unicodedata import combining, normalize
from uuid import uuid4


def _comparison_key(value: str) -> str:
    """Catch names likely to collide under a case/accent-insensitive DB collation."""
    return "".join(
        character for character in normalize("NFKD", value.casefold())
        if not combining(character)
    )


def build_employer_rename_plan(records):
    """Return normalized names and two-phase (old, temporary, target) renames.

    Every target is checked before any database write. Temporary names free
    existing code-based names, including cycles where one target is another
    employer's current name.
    """
    normalized = {}
    seen_targets = {}
    occupied = set()
    for record in records:
        old = str(record["name"])
        target = str(record.get("employer_name") or "").strip()
        if not target:
            raise ValueError(f"La empresa {old} no tiene employer_name.")
        if len(target) > 140:
            raise ValueError(f"El nombre de la empresa {old} supera 140 caracteres.")
        key = _comparison_key(target)
        if key in seen_targets:
            raise ValueError(
                f"Nombre de empresa duplicado: {target} ({seen_targets[key]} y {old})."
            )
        seen_targets[key] = old
        normalized[old] = target
        occupied.add(_comparison_key(old))
        occupied.add(key)

    renames = []
    for old, target in normalized.items():
        if old == target:
            continue
        temporary = f"CN-EMP-MIG-{uuid4().hex}"
        while _comparison_key(temporary) in occupied:
            temporary = f"CN-EMP-MIG-{uuid4().hex}"
        occupied.add(_comparison_key(temporary))
        renames.append((old, temporary, target))
    return normalized, renames


def employer_alias_index(records):
    """Resolve name/code labels only when they identify one employer."""
    aliases = {}
    ambiguous = set()
    for record in records:
        for field in ("name", "employer_name", "employer_code"):
            key = str(record.get(field) or "").strip().casefold()
            if not key or key in ambiguous:
                continue
            if key in aliases and aliases[key] != record["name"]:
                del aliases[key]
                ambiguous.add(key)
            else:
                aliases[key] = record["name"]
    return aliases, ambiguous
