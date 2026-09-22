"""USD cash distribution between paired deposits and collection claims.

This module deliberately has no Frappe dependency so the safety rules can be
tested without a site. A partial allocation is valid; a guessed split is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import clean_text

CAPACITY_EPSILON = 0.00005  # Half of the stored four-decimal USD precision.


def allocate_cash(
    deposits: Iterable[Mapping[str, Any]],
    claims: Iterable[Mapping[str, Any]],
    instructions: Iterable[Mapping[str, Any]] = (),
    blocked_deposit_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Allocate cash without ever exceeding a deposit or a claim.

    Each deposit/claim has a unique ``id`` and an ``amount_usd``. Claims carry
    ``references`` that permit automatic matching. Optional ``hints`` map a
    reference to uniquely linked core applications in USD. Instructions carry
    deposit_id, claim_id and amount_usd and are applied before automatic rules.
    """
    deposits = {str(row["id"]): row for row in deposits}
    claims = {str(row["id"]): row for row in claims}
    deposit_left = {key: round(float(row["amount_usd"]), 4) for key, row in deposits.items()}
    claim_left = {key: round(float(row["amount_usd"]), 4) for key, row in claims.items()}
    ledger = []
    instruction_results = {}
    blocked_deposits = {str(value) for value in blocked_deposit_ids}

    def book(deposit_id: str, claim_id: str, amount: float, origin: str):
        amount = round(amount, 4)
        if amount <= CAPACITY_EPSILON:
            return
        deposit_left[deposit_id] = round(deposit_left[deposit_id] - amount, 4)
        claim_left[claim_id] = round(claim_left[claim_id] - amount, 4)
        ledger.append(
            {
                "deposit_id": deposit_id,
                "claim_id": claim_id,
                "amount_usd": amount,
                "origin": origin,
            }
        )

    for instruction in instructions:
        name = str(instruction["id"])
        deposit_id = str(instruction.get("deposit_id") or "")
        claim_id = str(instruction.get("claim_id") or "")
        amount = round(float(instruction.get("amount_usd") or 0), 4)
        if deposit_id not in deposits or claim_id not in claims:
            instruction_results[name] = "Falta deposito o cobranza"
            if deposit_id in deposits:
                blocked_deposits.add(deposit_id)
            continue
        if amount <= CAPACITY_EPSILON:
            instruction_results[name] = "Importe invalido"
            blocked_deposits.add(deposit_id)
            continue
        if amount > deposit_left[deposit_id] + CAPACITY_EPSILON:
            instruction_results[name] = "Excede deposito"
            blocked_deposits.add(deposit_id)
            continue
        if amount > claim_left[claim_id] + CAPACITY_EPSILON:
            instruction_results[name] = "Excede cobranza"
            blocked_deposits.add(deposit_id)
            continue
        book(deposit_id, claim_id, amount, "Manual")
        instruction_results[name] = "Aplicada"

    # Repeat because one deposit can settle several claims and several deposits
    # can settle one claim. The loop only books positive amounts, so it ends.
    changed = True
    while changed:
        changed = False
        for deposit_id, deposit in deposits.items():
            if deposit_id in blocked_deposits or deposit_left[deposit_id] <= CAPACITY_EPSILON:
                continue
            reference = clean_text(deposit.get("reference"))
            if not reference:
                continue
            deposit_group = clean_text(deposit.get("group"))
            reference_groups = {
                clean_text(claim.get("group"))
                for claim in claims.values()
                if reference in {clean_text(value) for value in claim.get("references", ())}
                and clean_text(claim.get("group"))
            }
            if len(reference_groups) > 1:
                continue  # One reference spans employers: only explicit allocations are safe.
            if deposit_group and reference_groups and deposit_group not in reference_groups:
                continue
            candidates = [
                claim_id
                for claim_id, claim in claims.items()
                if claim_left[claim_id] > CAPACITY_EPSILON
                and reference in {clean_text(value) for value in claim.get("references", ())}
            ]
            if len(candidates) == 1:
                book(deposit_id, candidates[0], min(deposit_left[deposit_id], claim_left[candidates[0]]), "Automatica")
                changed = True
                continue
            if len(candidates) < 2:
                continue
            total = sum(claim_left[claim_id] for claim_id in candidates)
            if total <= deposit_left[deposit_id] + CAPACITY_EPSILON:
                for claim_id in candidates:
                    book(deposit_id, claim_id, claim_left[claim_id], "Automatica")
                changed = True
                continue
            hints = {
                claim_id: min(
                    claim_left[claim_id],
                    float(claims[claim_id].get("hints", {}).get(reference) or 0),
                )
                for claim_id in candidates
            }
            if (
                all(value > CAPACITY_EPSILON for value in hints.values())
                and sum(hints.values()) <= deposit_left[deposit_id] + CAPACITY_EPSILON
                and abs(sum(hints.values()) - deposit_left[deposit_id]) <= CAPACITY_EPSILON
            ):
                for claim_id, value in hints.items():
                    book(deposit_id, claim_id, value, "Automatica")
                changed = True

    return {
        "allocations": ledger,
        "deposit_remaining": deposit_left,
        "claim_remaining": claim_left,
        "instruction_results": instruction_results,
        "blocked_deposits": blocked_deposits,
    }


def can_document_surplus(
    unallocated_usd: float, already_documented_usd: float, requested_usd: float
) -> bool:
    """A company credit can only classify cash left after all distributions."""
    requested = float(requested_usd or 0)
    return (
        requested > CAPACITY_EPSILON
        and requested
        <= float(unallocated_usd or 0) - float(already_documented_usd or 0)
        + CAPACITY_EPSILON
    )
