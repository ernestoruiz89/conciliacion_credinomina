"""Conservative per-client matching for a company's remittance detail."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import canonical_identifier, clean_text
from credinomina_reconciliation.client_identity import matching_name
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money


EPSILON = 0.005


def detail_amount_usd(row: Mapping[str, Any], nio_per_usd: float = 0) -> tuple[float, str]:
    """USD is authoritative when both deduction columns are populated.

    The NIO column is not added to USD: the collection template commonly
    presents the same deduction in both currencies. A NIO-only row needs a
    documented deposit FX rate, never a guessed current market rate.
    """
    usd = money_float(row.get("deducted_usd"))
    nio = money_float(row.get("deducted_nio"))
    if usd < 0 or nio < 0:
        return 0, "Importe negativo"
    if usd > EPSILON:
        return money_float(usd), "US$ informado; C$ no se suma" if nio > EPSILON else "US$ informado"
    if nio > EPSILON:
        if nio_per_usd <= 0:
            return 0, "Falta tasa C$/US$ documentada"
        return money_float(decimal_value(nio) / decimal_value(nio_per_usd)), "C$ convertido con tasa documentada"
    return 0, "No deducido"


def _same_id(left: Any, right: Any) -> bool:
    return bool(clean_text(left)) and canonical_identifier(left) == canonical_identifier(right)


def _candidate_matches(row: Mapping[str, Any], claim: Mapping[str, Any]) -> bool:
    if row.get("client") and claim.get("client") and row["client"] != claim["client"]:
        return False
    loan = clean_text(row.get("loan_number"))
    client = clean_text(row.get("client_number"))
    employee = clean_text(row.get("employee_number"))
    national_id = clean_text(row.get("national_id"))
    has_identifier = any((loan, client, employee, national_id))
    if loan and not _same_id(loan, claim.get("loan_number")):
        return False
    if not has_identifier and not clean_text(row.get("client_name")):
        return False
    identity_match = False
    if client and claim.get("client_number"):
        if not _same_id(client, claim["client_number"]):
            return False
        identity_match = True
    if employee and claim.get("employee_number"):
        if not _same_id(employee, claim["employee_number"]):
            return False
        identity_match = True
    if national_id and claim.get("national_id"):
        if clean_text(national_id).casefold() != clean_text(claim["national_id"]).casefold():
            return False
        identity_match = True
    if (client or employee or national_id) and not identity_match and (
        not loan or claim.get("client_number") or claim.get("employee_number") or claim.get("national_id")
    ):
        return False
    # Company details often contain only the employee's name. Use an exact
    # normalized name or registered alias in that case, but never override a
    # conflicting credit/client identifier with a name match.
    if not has_identifier and not matching_name(
        row.get("client_name"), {
            "client_name": claim.get("client_name"),
            "client_aliases": claim.get("client_names") or (),
        },
    ):
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
    name_only = not any(clean_text(row.get(field)) for field in (
        "loan_number", "client_number", "employee_number", "national_id",
    ))
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
        return [], (
            "Sin coincidencia exacta por nombre/alias"
            if name_only else "Sin aplicación o cobranza identificable"
        )
    if len(matches) == 1:
        claim = matches[0]
        identity_note = (
            "; core sin identidad del cliente"
            if not claim.get("client_number") and not claim.get("employee_number") and not claim.get("national_id")
            else ""
        )
        if row.get("installment_number") and not claim.get("installment_number"):
            identity_note += "; core sin número de cuota"
        if money(amount_usd) > money(claim["amount_usd"]) + decimal_value(EPSILON):
            if (
                claim.get("kind") not in {"C", "H"}
                or len(claim.get("application_ids") or ()) != 1
                or money(amount_usd) - money(claim["amount_usd"])
                > money(tolerance_usd) + decimal_value(EPSILON)
            ):
                return [], "Importe supera el destino; revise partidas complementarias"
            return [{
                "claim_id": claim["id"],
                "amount_usd": money_float(claim["amount_usd"]),
            }], "Coincidencia única con diferencia de tolerancia" + identity_note
        reason = "Coincidencia única por nombre/alias" if name_only else "Coincidencia única"
        return [{"claim_id": claim["id"], "amount_usd": money_float(amount_usd)}], reason + identity_note
    kinds = {claim.get("kind") for claim in matches}
    if "C" in kinds and "H" in kinds:
        return [], "Coincidencia entre histórico y operativo: seleccione período"
    if not any((row.get("loan_number"), row.get("client_number"), row.get("employee_number"), row.get("national_id"))):
        people = {
            canonical_identifier(claim.get("client_number"))
            or canonical_identifier(claim.get("national_id"))
            or claim.get("client")
            for claim in matches
        }
        if len(people) != 1 or "" in people:
            return [], "Nombre compartido o sin identidad única; indique destinos manuales"
    total = sum_money(claim["amount_usd"] for claim in matches)
    if abs(total - money(amount_usd)) > decimal_value(EPSILON):
        return [], "Varias aplicaciones posibles; indique destinos manuales"
    return [
        {"claim_id": claim["id"], "amount_usd": money_float(claim["amount_usd"])}
        for claim in matches
    ], (
        "Varias aplicaciones cubiertas íntegramente por nombre/alias"
        if name_only else "Varias aplicaciones cubiertas íntegramente"
    )
