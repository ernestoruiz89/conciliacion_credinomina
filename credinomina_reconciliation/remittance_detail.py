"""Conservative per-client matching for a company's remittance detail."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import canonical_identifier, clean_text


EPSILON = 0.00005


def detail_amount_usd(row: Mapping[str, Any], nio_per_usd: float = 0) -> tuple[float, str]:
    """USD is authoritative when both deduction columns are populated.

    The NIO column is not added to USD: the collection template commonly
    presents the same deduction in both currencies. A NIO-only row needs a
    documented deposit FX rate, never a guessed current market rate.
    """
    usd = float(row.get("deducted_usd") or 0)
    nio = float(row.get("deducted_nio") or 0)
    if usd < 0 or nio < 0:
        return 0, "Importe negativo"
    if usd > EPSILON:
        return round(usd, 4), "US$ informado; C$ no se suma" if nio > EPSILON else "US$ informado"
    if nio > EPSILON:
        if nio_per_usd <= 0:
            return 0, "Falta tasa C$/US$ documentada"
        return round(nio / nio_per_usd, 4), "C$ convertido con tasa documentada"
    return 0, "No deducido"


def _same_id(left: Any, right: Any) -> bool:
    return bool(clean_text(left)) and canonical_identifier(left) == canonical_identifier(right)


def _candidate_matches(row: Mapping[str, Any], claim: Mapping[str, Any]) -> bool:
    loan = row.get("loan_number")
    client = row.get("client_number")
    national_id = row.get("national_id")
    if not loan or not (client or national_id):
        return False
    if not _same_id(loan, claim.get("loan_number")):
        return False
    identity_match = False
    if client and claim.get("client_number"):
        if not _same_id(client, claim["client_number"]):
            return False
        identity_match = True
    if national_id and claim.get("national_id"):
        if clean_text(national_id).casefold() != clean_text(claim["national_id"]).casefold():
            return False
        identity_match = True
    if not identity_match and (claim.get("client_number") or claim.get("national_id")):
        return False
    installment = row.get("installment_number")
    if installment and claim.get("installment_number") and not _same_id(
        installment, claim.get("installment_number")
    ):
        return False
    reference = clean_text(row.get("application_reference"))
    if reference and claim.get("kind") != "X" and reference not in {
        clean_text(value) for value in claim.get("references", ())
    }:
        return False
    return True


def suggest_detail_targets(
    row: Mapping[str, Any], claims: Iterable[Mapping[str, Any]],
    amount_usd: float, employer: str, period: str = "",
    tolerance_usd: float = 0,
) -> tuple[list[dict[str, Any]], str]:
    """Suggest only exact, uniquely attributable claims; never fuzzy-match names."""
    claims = [
        claim for claim in claims
        if clean_text(claim.get("group")) == clean_text(employer)
        and (not period or clean_text(claim.get("period")) == clean_text(period))
    ]
    row_key = clean_text(row.get("row_key"))
    if row_key:
        keyed = [
            claim for claim in claims
            if claim.get("kind") == "C" and clean_text(claim.get("row_key")) == row_key
        ]
        if len(keyed) > 1:
            return [], "Fila ID duplicada"
        if keyed:
            # The collection claim may be net of an administrative fee. Keep
            # complementary claims so a single company row can cover both.
            claims = keyed + [claim for claim in claims if claim.get("kind") == "X"]
    matches = [claim for claim in claims if _candidate_matches(row, claim)]
    if not matches:
        return [], "Sin aplicación o cobranza identificable"
    if len(matches) == 1:
        claim = matches[0]
        identity_note = (
            "; core sin identidad del cliente"
            if not claim.get("client_number") and not claim.get("national_id")
            else ""
        )
        if row.get("installment_number") and not claim.get("installment_number"):
            identity_note += "; core sin número de cuota"
        if amount_usd > float(claim["amount_usd"]) + EPSILON:
            if (
                claim.get("kind") not in {"C", "H"}
                or len(claim.get("application_ids") or ()) != 1
                or amount_usd - float(claim["amount_usd"]) > tolerance_usd + EPSILON
            ):
                return [], "Importe supera el destino; revise partidas complementarias"
            return [{
                "claim_id": claim["id"],
                "amount_usd": round(float(claim["amount_usd"]), 4),
            }], "Coincidencia única con diferencia de tolerancia" + identity_note
        return [{"claim_id": claim["id"], "amount_usd": round(amount_usd, 4)}], "Coincidencia única" + identity_note
    kinds = {claim.get("kind") for claim in matches}
    if "C" in kinds and "H" in kinds:
        return [], "Coincidencia entre histórico y operativo: seleccione período"
    total = round(sum(float(claim["amount_usd"]) for claim in matches), 4)
    if abs(total - amount_usd) > EPSILON:
        return [], "Varias aplicaciones posibles; indique destinos manuales"
    return [
        {"claim_id": claim["id"], "amount_usd": round(float(claim["amount_usd"]), 4)}
        for claim in matches
    ], "Varias aplicaciones cubiertas íntegramente"
