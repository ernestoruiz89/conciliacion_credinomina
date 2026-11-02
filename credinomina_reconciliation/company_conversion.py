"""Documented company differences, kept on the employer and never on a loan."""
import json

from credinomina_reconciliation.rounding import CENT_PRECISION, money, money_float, sum_money

KIND = "Diferencia documentada de empresa"
PREFIX = "X:CONVERSION:"


def documented_coverage(document, rows, claims, instructions, credits, reserved, allowed):
    """Use the accepted company documentation, without validating exchange rates.

    All identified application amounts remain complete. The signed company
    entry balances them to actual cash, pending the real core counterpart.
    """
    from credinomina_reconciliation.deposit_difference import is_difference
    from credinomina_reconciliation.remittance_detail import detail_amount_usd, suggest_detail_targets
    differences = [row for row in rows if is_difference(row)]
    if (not document.get("apply_fifo")
            or instructions or credits or len(differences) != 1
            or differences[0].get("difference_core_item")
            or not differences[0].get("difference_reason")):
        return None
    by_id = {claim["id"]: claim for claim in claims}
    used = dict(reserved)
    sources, usd, periods = set(), money(0), set()
    for row in rows:
        if is_difference(row):
            continue
        if row.get("manual_reconciliation_required"):
            return None
        amount, _reason = detail_amount_usd(row, float(document.get("fx_rate") or 0))
        targets, _reason = suggest_detail_targets(row, claims, amount, document.employer,
            [part.period for part in document.get("detail_periods") or []],
            allowed_groups=allowed, apply_fifo=True, reserved_amounts=used)
        if not targets or sum_money(target["amount_usd"] for target in targets) != money(amount):
            return None
        for target in targets:
            claim = by_id[target["claim_id"]]
            amount = money(target["amount_usd"])
            if (claim.get("kind") != "H" or claim["id"] in sources
                    or money(used.get(claim["id"]))
                    or amount != money(claim.get("amount_usd"))):
                return None
            sources.add(claim["id"])
            used[claim["id"]] = amount
            periods.add(claim.get("period"))
            usd += amount
    delta = money(document.get("amount_usd")) - usd
    if (not sources or len(periods) != 1 or not next(iter(periods))
            or delta != -CENT_PRECISION
            or money(differences[0].get("difference_amount_usd")) != delta):
        return None
    return dict(deposit=document.name, employer=document.employer, period=next(iter(periods)),
                difference_row=differences[0].name, amount_usd=money_float(delta),
                difference_reason=differences[0].get("difference_reason"),
                detail_basis=differences[0].get("difference_basis"),
                application_ids=sorted(claim[2:] for claim in sources))


def journal_entry(proof):
    return dict(tipo=KIND, empresa=proof["employer"], periodo=proof["period"],
                fila_diferencia=proof["difference_row"], importe_usd=proof["amount_usd"],
                origen=KIND, conversion_empresa=proof)


def from_rows(rows):
    result = {}
    for row in rows:
        raw = row.get("historical_detail") or "[]"
        entries = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Detalle de distribución inválido.")
        for entry in entries:
            proof = entry.get("conversion_empresa")
            if proof:
                if (not isinstance(proof, dict) or not proof.get("deposit") or not proof.get("period")
                        or money(proof.get("amount_usd")) != -CENT_PRECISION
                        or not isinstance(proof.get("application_ids"), list)
                        or not proof["application_ids"]
                        or row.get("name") and row.get("name") not in proof["application_ids"]
                        or proof["deposit"] in result and result[proof["deposit"]] != proof):
                    raise ValueError("La documentación de la diferencia de empresa está incompleta o es contradictoria.")
                result[proof["deposit"]] = proof
    return list(result.values())
