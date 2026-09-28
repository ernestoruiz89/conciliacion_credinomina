"""Plan safe CN Employer renames and resolve company aliases."""

from __future__ import annotations

from unicodedata import combining, normalize
from uuid import uuid4


def _comparison_key(value: str) -> str:
    """Catch names likely to collide under a case/accent-insensitive DB collation."""
    return "".join(
        character for character in normalize("NFKD", value.casefold())
        if not combining(character)
    )


def employer_label_key(value: str) -> str:
    """Normalize employer labels consistently for lookup and ambiguity checks."""
    return _comparison_key(" ".join(str(value or "").split()))


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
    """Resolve employer names, codes, and registered aliases unambiguously."""
    aliases = {}
    ambiguous = set()
    for record in records:
        labels = [record.get(field) for field in ("name", "employer_name", "employer_code")]
        labels.extend(
            alias.get("alias_name") if hasattr(alias, "get") else alias
            for alias in (record.get("aliases") or [])
        )
        for label in labels:
            key = employer_label_key(label)
            if not key or key in ambiguous:
                continue
            if key in aliases and aliases[key] != record["name"]:
                del aliases[key]
                ambiguous.add(key)
            else:
                aliases[key] = record["name"]
    return aliases, ambiguous


def attach_employer_aliases(records):
    """Attach child-table aliases to employer rows fetched with ``frappe.get_all``.

    Kept lazy-imported so the naming/indexing helpers remain usable in pure
    unit tests and migration scripts outside a running Frappe site.
    """
    records = list(records or [])
    if not records:
        return records

    import frappe

    employers_by_name = {record["name"]: record for record in records}
    aliases = frappe.get_all(
        "CN Employer Alias",
        filters={"parent": ["in", list(employers_by_name)]},
        fields=["parent", "alias_name"],
        limit_page_length=100000,
    )
    for record in records:
        record["aliases"] = []
    for alias in aliases:
        employer = employers_by_name.get(alias.parent)
        alias_name = str(alias.alias_name or "").strip()
        if employer and alias_name:
            employer["aliases"].append(alias_name)
    return records
