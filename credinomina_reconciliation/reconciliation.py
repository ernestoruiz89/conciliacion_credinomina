from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import (
    SOURCE_ACCOUNTING,
    SOURCE_DEPOSITS,
    SOURCE_TRANSACTIONS,
    canonical_identifier,
    clean_text,
)


AMOUNT_TOLERANCE = 0.01


def same_amount(left: Any, right: Any, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    return abs(float(left or 0) - float(right or 0)) <= tolerance


def same_exact_money(left: Any, right: Any) -> bool:
    """Four-decimal equality, before any explicit reconciliation tolerance."""
    return abs(round(float(left or 0), 4) - round(float(right or 0), 4)) <= 0.00005


def converted_amount(row: Mapping[str, Any], target_currency: str) -> float | None:
    """Value a deposit in the requested currency using documented evidence.

    The rate is NIO per USD. The source-file equivalent has priority over a
    manually entered rate; an undocumented rate is never used for matching.
    """
    native_currency = clean_text(row.get("currency")).upper()
    target_currency = clean_text(target_currency).upper()
    amount = float(row.get("amount") or 0)
    if native_currency == target_currency:
        return amount
    if {native_currency, target_currency} != {"NIO", "USD"}:
        return None
    if (
        clean_text(row.get("equivalent_currency")).upper() == target_currency
        and float(row.get("equivalent_amount") or 0) > 0
        and clean_text(row.get("fx_basis"))
    ):
        return float(row.get("equivalent_amount") or 0)
    rate = float(row.get("manual_fx_rate") or 0)
    if rate <= 0 or not clean_text(row.get("manual_fx_evidence")):
        return None
    return amount / rate if native_currency == "NIO" else amount * rate


def documented_rate(row: Mapping[str, Any]) -> float | None:
    rate = float(row.get("fx_rate") or 0)
    if rate > 0 and clean_text(row.get("fx_basis")):
        return rate
    rate = float(row.get("manual_fx_rate") or 0)
    if rate > 0 and clean_text(row.get("manual_fx_evidence")):
        return rate
    return None


def deposit_pair_result(
    accounting: Mapping[str, Any], bank: Mapping[str, Any]
) -> tuple[bool, str]:
    """Match one accounting deposit to one bank row, including cross currency."""
    reference = clean_text(accounting.get("reference"))
    if not reference or reference != clean_text(bank.get("reference")):
        return False, "La referencia bancaria no coincide."
    account_currency = clean_text(accounting.get("currency")).upper()
    bank_currency = clean_text(bank.get("currency")).upper()
    if account_currency == bank_currency:
        if same_exact_money(accounting.get("amount"), bank.get("amount")):
            return True, "Referencia, moneda e importe coinciden."
        return False, "El importe del banco difiere del movimiento contable."
    comparisons = []
    account_in_bank = converted_amount(accounting, bank_currency)
    if account_in_bank is not None:
        comparisons.append(same_exact_money(account_in_bank, bank.get("amount")))
    bank_in_account = converted_amount(bank, account_currency)
    if bank_in_account is not None:
        comparisons.append(same_exact_money(bank_in_account, accounting.get("amount")))
    if not comparisons:
        return False, "Falta un tipo de cambio documentado para conciliar las monedas."
    if all(comparisons):
        return True, "Referencia e importes equivalentes coinciden con tipo de cambio documentado."
    return False, "Los importes convertidos o tipos de cambio no coinciden."


def narrow_deposit_candidates_by_date(source, candidates):
    """Use an exact posting date only to resolve an otherwise repeated deposit."""
    candidates = list(candidates)
    if len(candidates) <= 1 or not source.get("event_date"):
        return candidates
    dated = [
        candidate for candidate in candidates
        if candidate.get("event_date")
        and str(candidate.get("event_date"))[:10] == str(source.get("event_date"))[:10]
    ]
    return dated if len(dated) == 1 else candidates


def settlement_result(
    applications: Iterable[Mapping[str, Any]],
    deposit_pairs: Iterable[tuple[Mapping[str, Any], Mapping[str, Any]]],
    complementary_items: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compare USD loan applications plus documented other income with cash."""
    applications = list(applications)
    deposit_pairs = list(deposit_pairs)
    complementary_items = list(complementary_items)
    if (not applications and not complementary_items) or not deposit_pairs:
        return {"matched": False, "reason": "Faltan aplicaciones o depositos conciliados."}
    target_currency = "USD"
    applied = 0.0
    for row in applications:
        value = converted_amount(row, target_currency)
        if value is None:
            return {
                "matched": False,
                "reason": "Falta tipo de cambio documentado para expresar la aplicacion en US$.",
            }
        applied += value
    complementary_usd = sum(
        float(item.get("amount_usd") or 0) for item in complementary_items
    )
    deposited = 0.0
    for accounting, bank in deposit_pairs:
        account_value = converted_amount(accounting, target_currency)
        bank_value = converted_amount(bank, target_currency)
        if account_value is None and bank_value is None:
            return {
                "matched": False,
                "reason": "Falta tipo de cambio documentado para comparar deposito y aplicaciones.",
            }
        if (
            account_value is not None
            and bank_value is not None
            and not same_exact_money(account_value, bank_value)
        ):
            return {
                "matched": False,
                "reason": "El equivalente cambiario del banco difiere del movimiento contable.",
            }
        deposited += account_value if account_value is not None else bank_value
    reconciled_total = applied + complementary_usd
    difference = round(deposited - reconciled_total, 4)
    return {
        "matched": same_exact_money(reconciled_total, deposited),
        "reason": (
            "La remesa cubre las aplicaciones y partidas complementarias."
            if same_exact_money(reconciled_total, deposited)
            else "El deposito difiere de las aplicaciones y partidas complementarias."
        ),
        "currency": target_currency,
        "applied": round(applied, 4),
        "complementary": round(complementary_usd, 4),
        "reconciled_total": round(reconciled_total, 4),
        "deposited": round(deposited, 4),
        "difference": difference,
    }


def complementary_matches_collection(
    item: Mapping[str, Any], target: Mapping[str, Any], reference: str
) -> bool:
    """Allocate other income to a collection row only with an explicit loan link."""
    if clean_text(item.get("reference")) != clean_text(reference):
        return False
    loan = canonical_identifier(item.get("loan_number"))
    if not loan or loan != canonical_identifier(target.get("loan_number")):
        return False
    for field in ("client_number", "installment_number"):
        value = canonical_identifier(item.get(field))
        if value and value != canonical_identifier(target.get(field)):
            return False
    if item.get("period") and item.get("period") != target.get("parent"):
        return False
    if item.get("employer") and item.get("employer") != target.get("employer"):
        return False
    return True


def matching_exception_notes(
    *,
    expected_usd: float,
    deducted_usd: float,
    applied_usd: float,
    complementary_usd: float,
    remitted_usd: float,
    deduction_notes: Iterable[Mapping[str, Any]] = (),
    application_notes: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Reuse first-stage explanations only for the same USD shortfall.

    Notes are references to an existing exception/comment, not a new payment
    or a newly resolved exception. No remittance means nothing to annotate.
    """
    expected = float(expected_usd or 0)
    remitted = float(remitted_usd or 0)
    payment_gap = round(max(expected - remitted, 0), 4)
    if remitted <= AMOUNT_TOLERANCE or payment_gap <= AMOUNT_TOLERANCE:
        return []
    deduction_gap = round(max(expected - float(deducted_usd or 0), 0), 4)
    application_gap = round(
        max(expected - float(applied_usd or 0) - float(complementary_usd or 0), 0),
        4,
    )
    matched = []
    seen_comments = set()
    for origin, gap, notes in (
        ("Cobranza vs aplicacion", application_gap, application_notes),
        ("Cobranza vs deduccion", deduction_gap, deduction_notes),
    ):
        if gap <= AMOUNT_TOLERANCE or not same_amount(gap, payment_gap):
            continue
        for note in notes:
            comment = clean_text(note.get("comment"))
            key = comment.casefold()
            if not comment or key in seen_comments:
                continue
            seen_comments.add(key)
            matched.append(
                {
                    "origin": origin,
                    "gap_usd": payment_gap,
                    "comment": comment,
                    "exception_id": clean_text(note.get("exception_id")),
                }
            )
    return matched


def classify_deduction(
    *,
    expected_usd: float = 0,
    expected_nio: float = 0,
    deducted_usd: float = 0,
    deducted_nio: float = 0,
) -> str:
    expected_usd = float(expected_usd or 0)
    expected_nio = float(expected_nio or 0)
    deducted_usd = float(deducted_usd or 0)
    deducted_nio = float(deducted_nio or 0)
    if deducted_usd < 0 or deducted_nio < 0:
        return "Importe invalido"
    if deducted_usd <= AMOUNT_TOLERANCE and deducted_nio <= AMOUNT_TOLERANCE:
        return "No deducido"

    comparisons = []
    if deducted_usd > AMOUNT_TOLERANCE and expected_usd > AMOUNT_TOLERANCE:
        comparisons.append((deducted_usd, expected_usd))
    if deducted_nio > AMOUNT_TOLERANCE and expected_nio > AMOUNT_TOLERANCE:
        comparisons.append((deducted_nio, expected_nio))
    if not comparisons:
        return "Moneda no coincide"

    states = []
    for deducted, expected in comparisons:
        if same_amount(deducted, expected):
            states.append("Deduccion total")
        elif deducted < expected:
            states.append("Deduccion parcial")
        else:
            states.append("Deduccion en exceso")
    return states[0] if len(set(states)) == 1 else "Importes inconsistentes"


def match_collection_record(
    response: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, str]:
    candidates = list(candidates)
    row_key = clean_text(response.get("row_key"))
    if row_key:
        exact = [row for row in candidates if clean_text(row.get("row_key")) == row_key]
        if len(exact) == 1:
            return exact[0], "Fila ID"
        if len(exact) > 1:
            return None, "Fila ID duplicada"

    loan = clean_text(response.get("loan_number"))
    installment = clean_text(response.get("installment_number"))
    client = clean_text(response.get("client_number"))
    national_id = clean_text(response.get("national_id"))

    strategies = []
    if client and loan:
        strategies.append(
            (
                "cliente, credito y cuota",
                lambda row: canonical_identifier(row.get("client_number"))
                == canonical_identifier(client)
                and canonical_identifier(row.get("loan_number"))
                == canonical_identifier(loan)
                and (
                    not installment
                    or canonical_identifier(row.get("installment_number"))
                    == canonical_identifier(installment)
                ),
            )
        )
    if national_id and loan:
        strategies.append(
            (
                "cedula, credito y cuota",
                lambda row: clean_text(row.get("national_id")).casefold()
                == national_id.casefold()
                and canonical_identifier(row.get("loan_number"))
                == canonical_identifier(loan)
                and (
                    not installment
                    or canonical_identifier(row.get("installment_number"))
                    == canonical_identifier(installment)
                ),
            )
        )
    if loan:
        strategies.append(
            (
                "credito y cuota",
                lambda row: canonical_identifier(row.get("loan_number"))
                == canonical_identifier(loan)
                and (
                    not installment
                    or canonical_identifier(row.get("installment_number"))
                    == canonical_identifier(installment)
                ),
            )
        )

    for label, predicate in strategies:
        exact = [row for row in candidates if predicate(row)]
        if len(exact) == 1:
            return exact[0], label
        if len(exact) > 1:
            return None, f"Coincidencia ambigua por {label}"
    return None, "Sin coincidencia exacta"


def source_priority(source_type: str, event_type: str) -> int:
    if source_type == SOURCE_ACCOUNTING:
        return 10
    if source_type == SOURCE_TRANSACTIONS and event_type == "Aplicacion":
        return 20
    if source_type == SOURCE_DEPOSITS and event_type == "Deposito":
        return 20
    return 90


def duplicate_business_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    event_type = clean_text(row.get("event_type"))
    if event_type == "Aplicacion":
        return (
            event_type,
            canonical_identifier(row.get("loan_number")),
            clean_text(row.get("reference")),
            clean_text(row.get("currency")).upper(),
            round(float(row.get("amount") or 0), 2),
            str(row.get("event_date") or "")[:10],
            clean_text(row.get("voucher")),
        )
    return (
        event_type,
        clean_text(row.get("reference")),
        clean_text(row.get("currency")).upper(),
        round(float(row.get("amount") or 0), 2),
    )


def operational_status(
    *, expected: float, deducted: float, applied: float, remitted: float
) -> str:
    if deducted + AMOUNT_TOLERANCE < expected:
        return "Pendiente del trabajador"
    if applied + AMOUNT_TOLERANCE < deducted:
        return "Deducido, pendiente de aplicar en core"
    if remitted + AMOUNT_TOLERANCE < deducted:
        return "Aplicado, empresa por remitir"
    return "Conciliado"
