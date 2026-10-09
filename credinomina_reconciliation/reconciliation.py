from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.client_identity import matching_name
from credinomina_reconciliation.collection_identity import collection_client_number
from credinomina_reconciliation.parsers import (
    canonical_credit_number, canonical_identifier,
    clean_text,
)
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money


AMOUNT_TOLERANCE = 0.01
def net_application_amount(row):
    """USD-normalized source amount after confirmed reductions; preserve evidence."""
    return money_float(max(money(row.get("amount")) - money(row.get("application_adjustment_usd")), 0))


def remittance_fx_basis(remittance: Mapping[str, Any]) -> str:
    """Use the entered positive remittance rate; a written source is optional."""
    rate = decimal_value(remittance.get("fx_rate") or remittance.get("manual_fx_rate"))
    if rate <= 0:
        return ""
    support = clean_text(remittance.get("support_file"))
    notes = clean_text(remittance.get("notes"))
    basis = f"Tasa C$/US$ ingresada: {rate}"
    if notes:
        basis += f"; observación: {notes}"
    if support:
        basis += f"; soporte: {support}"
    return basis


def application_matches_collection(
    application: Mapping[str, Any], collection: Mapping[str, Any],
    client_aliases: Iterable[str] = (),
) -> bool:
    """Match core applications by available identifiers, name only as a last resort."""
    loan = canonical_credit_number(application.get("loan_number"))
    client = canonical_identifier(application.get("client_number"))
    employee = canonical_identifier(application.get("employee_number"))
    national_id = canonical_identifier(application.get("national_id"))
    if loan and loan != canonical_credit_number(collection.get("loan_number")):
        return False
    matched_identifier = bool(loan)
    for field, value in (
        ("client_number", client), ("employee_number", employee), ("national_id", national_id),
    ):
        collection_value = canonical_identifier(collection_client_number(collection) if field == "client_number" else collection.get(field))
        # Missing employer-file data is not a conflict, but cannot prove identity.
        if value and collection_value:
            if value != collection_value:
                return False
            matched_identifier = True
    if not (loan or client or employee or national_id):
        return matching_name(application.get("client_name"), {
            "client_name": collection.get("client_name"),
            "client_aliases": tuple(client_aliases),
        })
    return matched_identifier


def same_amount(left: Any, right: Any, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    return abs(money(left) - money(right)) <= decimal_value(tolerance)


def same_exact_money(left: Any, right: Any) -> bool:
    """Compare amounts at the persisted two-decimal monetary precision."""
    return money(left) == money(right)


def converted_amount(row: Mapping[str, Any], target_currency: str) -> float | None:
    value = _original_converted_amount(row, target_currency)
    adjustment = money(row.get("application_adjustment_usd"))
    if value is None or not adjustment or row.get("event_type") != "Aplicacion":
        return value
    if target_currency.upper() == "USD":
        return money_float(max(money(value) - adjustment, 0))
    original_usd = _original_converted_amount(row, "USD")
    if not original_usd:
        return None
    return money_float(money(value) * max(money(original_usd) - adjustment, 0) / money(original_usd))


def _original_converted_amount(row: Mapping[str, Any], target_currency: str) -> float | None:
    """Value a deposit in the requested currency using its conversion rate.

    The rate is NIO per USD. The source-file equivalent has priority over a
    manually entered rate.
    """
    native_currency = clean_text(row.get("currency")).upper()
    target_currency = clean_text(target_currency).upper()
    amount = decimal_value(row.get("amount"))
    if native_currency == target_currency:
        return money_float(amount)
    if {native_currency, target_currency} != {"NIO", "USD"}:
        return None
    if (
        clean_text(row.get("equivalent_currency")).upper() == target_currency
        and money(row.get("equivalent_amount")) > 0
        and clean_text(row.get("fx_basis"))
    ):
        return money_float(row.get("equivalent_amount"))
    rate = decimal_value(row.get("manual_fx_rate"))
    if rate <= 0:
        return None
    converted = amount / rate if native_currency == "NIO" else amount * rate
    return money_float(converted)


def documented_rate(row: Mapping[str, Any]) -> float | None:
    rate = float(row.get("fx_rate") or 0)
    if rate > 0 and clean_text(row.get("fx_basis")):
        return rate
    rate = float(row.get("manual_fx_rate") or 0)
    if rate > 0:
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
        return False, "Falta un tipo de cambio para conciliar las monedas."
    if all(comparisons):
        return True, "Referencia e importes equivalentes coinciden tras convertir las monedas."
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
    applied_values = []
    for row in applications:
        value = converted_amount(row, target_currency)
        if value is None:
            return {
                "matched": False,
                "reason": "Falta tipo de cambio para expresar la aplicacion en US$.",
            }
        applied_values.append(value)
    applied = sum_money(applied_values)
    complementary_usd = sum_money(item.get("amount_usd") for item in complementary_items)
    deposited_values = []
    for accounting, bank in deposit_pairs:
        account_value = converted_amount(accounting, target_currency)
        bank_value = converted_amount(bank, target_currency)
        if account_value is None and bank_value is None:
            return {
                "matched": False,
                "reason": "Falta tipo de cambio para comparar deposito y aplicaciones.",
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
        deposited_values.append(account_value if account_value is not None else bank_value)
    deposited = sum_money(deposited_values)
    reconciled_total = applied + complementary_usd
    difference = money(deposited - reconciled_total)
    return {
        "matched": same_exact_money(reconciled_total, deposited),
        "reason": (
            "El depósito cubre las aplicaciones y partidas complementarias."
            if same_exact_money(reconciled_total, deposited)
            else "El deposito difiere de las aplicaciones y partidas complementarias."
        ),
        "currency": target_currency,
        "applied": money_float(applied),
        "complementary": money_float(complementary_usd),
        "reconciled_total": money_float(reconciled_total),
        "deposited": money_float(deposited),
        "difference": money_float(difference),
    }


def complementary_matches_collection(
    item: Mapping[str, Any], target: Mapping[str, Any], reference: str
) -> bool:
    """Allocate other income to a collection row only with an explicit loan link."""
    if clean_text(item.get("reference")) != clean_text(reference):
        return False
    loan = canonical_credit_number(item.get("loan_number"))
    if not loan or loan != canonical_credit_number(target.get("loan_number")):
        return False
    for field in ("client_number", "installment_number"):
        value = canonical_identifier(item.get(field))
        stored = collection_client_number(target) if field == "client_number" else target.get(field)
        if value and value != canonical_identifier(stored):
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
    expected = money(expected_usd)
    remitted = money(remitted_usd)
    payment_gap = money(max(expected - remitted, 0))
    if remitted <= AMOUNT_TOLERANCE or payment_gap <= AMOUNT_TOLERANCE:
        return []
    deduction_gap = money(max(expected - money(deducted_usd), 0))
    application_gap = money(max(expected - money(applied_usd) - money(complementary_usd), 0))
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
    expected_usd = money_float(expected_usd)
    expected_nio = money_float(expected_nio)
    deducted_usd = money_float(deducted_usd)
    deducted_nio = money_float(deducted_nio)
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
            row = exact[0]
            for field in ("loan_number", "client_number", "employee_number", "national_id"):
                normalize = canonical_credit_number if field == "loan_number" else canonical_identifier
                incoming = normalize(response.get(field))
                stored = normalize(collection_client_number(row) if field == "client_number" else row.get(field))
                if incoming and stored and incoming != stored:
                    return None, "La Fila ID contradice los identificadores del cliente o crédito"
            return exact[0], "Fila ID"
        if len(exact) > 1:
            return None, "Fila ID duplicada"
        return None, "Fila ID no encontrada en la cobranza del período"

    loan = clean_text(response.get("loan_number"))
    installment = clean_text(response.get("installment_number"))
    client = clean_text(response.get("client_number"))
    employee = clean_text(response.get("employee_number"))
    national_id = clean_text(response.get("national_id"))
    def identifiers_compatible(row):
        return not (
            client and collection_client_number(row)
            and canonical_identifier(collection_client_number(row)) != canonical_identifier(client)
        ) and not (
            employee and row.get("employee_number")
            and canonical_identifier(row.get("employee_number")) != canonical_identifier(employee)
        ) and not (
            national_id and row.get("national_id")
            and canonical_identifier(row.get("national_id")) != canonical_identifier(national_id)
        )

    strategies = []
    if client and loan:
        strategies.append(
            (
                "cliente, credito y cuota",
                lambda row: canonical_identifier(collection_client_number(row))
                == canonical_identifier(client)
                and identifiers_compatible(row)
                and canonical_credit_number(row.get("loan_number"))
                == canonical_credit_number(loan)
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
                and identifiers_compatible(row)
                and canonical_credit_number(row.get("loan_number"))
                == canonical_credit_number(loan)
                and (
                    not installment
                    or canonical_identifier(row.get("installment_number"))
                    == canonical_identifier(installment)
                ),
            )
        )
    if employee and loan:
        strategies.append((
            "número de empleado, crédito y cuota",
            lambda row: canonical_identifier(row.get("employee_number"))
            == canonical_identifier(employee)
            and identifiers_compatible(row)
            and canonical_credit_number(row.get("loan_number")) == canonical_credit_number(loan)
            and (not installment or canonical_identifier(row.get("installment_number"))
                 == canonical_identifier(installment)),
        ))
    if loan and not (client or employee or national_id):
        strategies.append(
            (
                "credito y cuota",
                lambda row: canonical_credit_number(row.get("loan_number"))
                == canonical_credit_number(loan)
                and (
                    not installment
                    or canonical_identifier(row.get("installment_number"))
                    == canonical_identifier(installment)
                ),
            )
        )
    if client and not loan:
        strategies.append((
            "número de cliente",
            lambda row: canonical_identifier(collection_client_number(row))
            == canonical_identifier(client)
            and identifiers_compatible(row)
            and (not installment or canonical_identifier(row.get("installment_number"))
                 == canonical_identifier(installment)),
        ))
    if national_id and not loan:
        strategies.append((
            "cédula",
            lambda row: canonical_identifier(row.get("national_id"))
            == canonical_identifier(national_id)
            and identifiers_compatible(row)
            and (not installment or canonical_identifier(row.get("installment_number"))
                 == canonical_identifier(installment)),
        ))
    if employee and not loan:
        strategies.append((
            "número de empleado",
            lambda row: canonical_identifier(row.get("employee_number"))
            == canonical_identifier(employee)
            and identifiers_compatible(row)
            and (not installment or canonical_identifier(row.get("installment_number"))
                 == canonical_identifier(installment)),
        ))
    if not any((loan, client, employee, national_id)) and response.get("client_name"):
        strategies.append((
            "nombre o alias único",
            lambda row: matching_name(response["client_name"], row)
            and (not installment or canonical_identifier(row.get("installment_number"))
                 == canonical_identifier(installment)),
        ))

    for label, predicate in strategies:
        exact = [row for row in candidates if predicate(row)]
        if len(exact) == 1:
            return exact[0], label
        if len(exact) > 1:
            return None, f"Coincidencia ambigua por {label}"
    return None, "Sin coincidencia exacta"


def duplicate_business_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    event_type = clean_text(row.get("event_type"))
    if event_type == "Aplicacion":
        return (
            event_type,
            canonical_credit_number(row.get("loan_number")),
            clean_text(row.get("reference")),
            clean_text(row.get("currency")).upper(),
            money(row.get("amount")),
            str(row.get("event_date") or "")[:10],
            clean_text(row.get("voucher")),
        )
    return (
        event_type,
        clean_text(row.get("reference")),
        clean_text(row.get("currency")).upper(),
        money(row.get("amount")),
    )


def operational_status(
    *, expected: float, deducted: float, applied: float, remitted: float
) -> str:
    if deducted + AMOUNT_TOLERANCE < expected:
        return "Cobranza no deducida; revisar primera conciliación"
    if applied + AMOUNT_TOLERANCE < deducted:
        return "Deducido, pendiente de aplicar en core"
    if remitted + AMOUNT_TOLERANCE < deducted:
        return "Aplicado, empresa por remitir"
    return "Conciliado"
