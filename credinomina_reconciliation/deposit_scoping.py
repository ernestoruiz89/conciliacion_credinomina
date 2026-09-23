"""Resolve the employer of a registered deposit."""

from __future__ import annotations

from typing import Any, Mapping

from credinomina_reconciliation.parsers import clean_text


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
