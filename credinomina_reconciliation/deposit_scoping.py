"""Classify mixed monthly bank files without discarding their source rows."""

from __future__ import annotations

import re
from typing import Any, Mapping

from credinomina_reconciliation.parsers import clean_text


def duplicate_deposit_key(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Only exact, identified bank rows repeated in another import are duplicates."""
    reference = clean_text(row.get("reference"))
    event_date = clean_text(row.get("event_date"))[:10]
    if not reference or not event_date:
        return None
    return (
        event_date,
        reference,
        clean_text(row.get("voucher")).casefold(),
        clean_text(row.get("currency")),
        f"{float(row.get('amount') or 0):.4f}",
        clean_text(row.get("description")).casefold(),
        clean_text(row.get("client_name")).casefold(),
        clean_text(row.get("loan_number")),
    )


def deposit_scope(
    bank: Mapping[str, Any],
    account: Mapping[str, Any] | None,
    employer_aliases: Mapping[str, str],
    ambiguous_aliases: set[str] | None = None,
) -> str:
    """Return Conciliar, Excluir or Revisar for one row in Depósito."""
    override = clean_text(bank.get("deposit_scope"))
    if override in {"Conciliar", "Excluir"}:
        return override

    labels = [clean_text(bank.get("client_name")), clean_text(bank.get("employer_text"))]
    if account:
        labels.append(clean_text(account.get("employer_text")))
    ambiguous_aliases = ambiguous_aliases or set()
    if any(label.casefold() in ambiguous_aliases for label in labels if label):
        return "Revisar"
    if any(label.casefold() in employer_aliases for label in labels if label):
        return "Conciliar"
    descriptions = [clean_text(bank.get("description"))]
    if account:
        descriptions.append(clean_text(account.get("description")))
    if any(re.search(r"\bCONVENIO\b", value, re.IGNORECASE) for value in labels + descriptions):
        return "Conciliar"

    client = clean_text(bank.get("client_name"))
    if not client or client.casefold() in {"no identificado", "n/i", "sin identificar"}:
        return "Revisar"
    return "Excluir"


def resolved_deposit_employer(
    account: Mapping[str, Any],
    bank: Mapping[str, Any],
    aliases: Mapping[str, str],
    ambiguous: set[str],
) -> tuple[str | None, bool]:
    """Return unique employer and whether automatic distribution must be blocked."""
    labels = {
        clean_text(value).casefold()
        for value in (account.get("employer_text"), bank.get("employer_text"))
        if clean_text(value)
    }
    matches = {aliases[label] for label in labels if label in aliases}
    if labels & ambiguous or len(matches) != 1:
        return None, True
    return next(iter(matches)), False
