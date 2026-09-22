"""Conservative checks for using an exact deposit as provisional payroll detail."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from credinomina_reconciliation.reconciliation import (
    AMOUNT_TOLERANCE,
    converted_amount,
    deposit_pair_result,
    same_exact_money,
)


def recognition_reason(
    rows: Sequence[Mapping], accounting: Mapping, bank: Mapping
) -> str:
    """An empty reason means the full collection and verified deposit coincide."""
    if not rows:
        return "El período no tiene filas de cobranza."
    if any(row.get("deduction_status") != "Pendiente de detalle" for row in rows):
        return "La cobranza ya tiene deducciones registradas."
    if any(float(row.get("expected_usd") or 0) <= AMOUNT_TOLERANCE for row in rows):
        return "Cada fila debe tener un importe de cobranza en US$."
    paired, reason = deposit_pair_result(accounting, bank)
    if not paired:
        return reason
    if any(
        float(deposit.get("allocated_usd") or 0) > AMOUNT_TOLERANCE
        or float(deposit.get("justified_surplus_usd") or 0) > AMOUNT_TOLERANCE
        for deposit in (accounting, bank)
    ):
        return "El depósito ya tiene una distribución o un saldo a favor documentado."
    account_usd = converted_amount(accounting, "USD")
    bank_usd = converted_amount(bank, "USD")
    if account_usd is None and bank_usd is None:
        return "Falta tipo de cambio documentado para expresar el depósito en US$."
    if account_usd is not None and bank_usd is not None and not same_exact_money(account_usd, bank_usd):
        return "Los equivalentes en US$ del banco y contabilidad difieren."
    expected_usd = sum(float(row.get("expected_usd") or 0) for row in rows)
    deposited_usd = account_usd if account_usd is not None else bank_usd
    if not same_exact_money(expected_usd, deposited_usd):
        return "El depósito en US$ no coincide con el total de la cobranza."
    if all(float(row.get("expected_nio") or 0) > AMOUNT_TOLERANCE for row in rows):
        expected_nio = sum(float(row.get("expected_nio") or 0) for row in rows)
        for deposit in (accounting, bank):
            if deposit.get("currency") == "NIO" and not same_exact_money(
                expected_nio, deposit.get("amount")
            ):
                return "El depósito en C$ no coincide con el total de la cobranza en C$."
    return ""
