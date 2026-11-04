"""Informational row coverage; linked destinations do not imply settlement."""
import json
from collections import defaultdict

from credinomina_reconciliation.rounding import money, money_float, sum_money


def confirmed_fifo_by_row(document):
    """Persisted cash is stronger evidence than the editable display cache."""
    raw = document.get("allocation_detail")
    if not raw:
        return None
    try:
        ledger = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None  # Financial reconciliation separately rejects corrupt ledgers.
    if not isinstance(ledger, list):
        return None
    from credinomina_reconciliation.allocation_origin import FIFO
    result = defaultdict(list)
    for entry in ledger:
        if (isinstance(entry, dict) and entry.get("origen") == FIFO and entry.get("tipo") == "Aplicacion historica"
                and entry.get("fila_detalle") and entry.get("aplicacion_id")
                and not entry.get("reclassification_operation_id") and money(entry.get("importe_usd")) > 0):
            result[entry["fila_detalle"]].append(dict(claim_id="H:" + entry["aplicacion_id"],
                amount_usd=entry["importe_usd"], fifo_applications=entry.get("aplicaciones_fifo"), result="Aplicada"))
    return result


def _historical_claim(entry):
    claim = entry.get("claim_id") or ("H:" + entry.get("historical_application") if entry.get("historical_application") else "")
    return claim if claim.startswith("H:") else ""


def selected_links(manual, matched, confirmed_fifo=None):
    if isinstance(matched, str):
        try:
            matched = json.loads(matched or "[]")
        except (ValueError, TypeError):
            matched = []
    # Removed manual instructions must not be resurrected from the cache.
    if not isinstance(matched, list):
        matched = []
    # Core reclassification instructions are not editable manual targets.
    # Keep them when the user edits other targets, but never count the same
    # instruction twice when the reconciliation engine already supplied it.
    internal = [entry for entry in matched if isinstance(entry, dict)
                and str(entry.get("instruction_id") or "").startswith("RECLASS:")]
    if manual:
        ids = {entry.get("id") or entry.get("instruction_id") for entry in manual}
        result = list(manual) + [entry for entry in internal if entry.get("instruction_id") not in ids]
    else:
        result = list(internal)
    automatic = confirmed_fifo if confirmed_fifo is not None else [entry for entry in matched if isinstance(entry, dict)
        and (not entry.get("instruction_id") or str(entry.get("instruction_id")).startswith("RETAINED:"))
        and (not manual or _historical_claim(entry) and entry.get("fifo_applications"))]
    instructed = defaultdict(lambda: money(0))
    for entry in manual:
        instructed[_historical_claim(entry)] += money(entry.get("amount_usd"))
    # A manual instruction for the same claim replaces that claim's planned
    # total; another claim supplements the confirmed FIFO cash. Never add a
    # RETAINED instruction twice when the engine already supplied it.
    for entry in automatic:
        amount = money(entry.get("amount_usd"))
        claim = _historical_claim(entry)
        overlap = min(max(instructed[claim], money(0)), amount) if claim and amount > 0 else money(0)
        instructed[claim] -= overlap
        if amount != overlap:
            result.append({**entry, "amount_usd": money_float(amount - overlap)})
    return result


def linked_balance(amount, manual, matched, confirmed_fifo=None):
    linked = sum_money(entry.get("amount_usd") for entry in selected_links(manual, matched, confirmed_fifo))
    return {"linked_usd": money_float(linked), "pending_usd": money_float(money(amount) - linked)}


def update_detail_balances(document):
    credits = {}
    company_credits = {}
    if document.get("doctype") == "CN Remittance Allocation" and document.get("name") and document.get("docstatus") == 1:
        from credinomina_reconciliation.client_credit import load_credits, row_credit_amounts, RESULT
        items = [item for item in load_credits([document.name]) if item.result == RESULT]
        credits = row_credit_amounts([item for item in items if item.category == "Saldo a favor del cliente"], document.get("detail_rows") or [])
        company_credits = row_credit_amounts([item for item in items if item.category == "Saldo a favor de la empresa"], document.get("detail_rows") or [])
    by_row = defaultdict(list)
    confirmed = confirmed_fifo_by_row(document)
    for target in document.get("targets") or []:
        if target.get("detail_row"):
            by_row[target.get("detail_row")].append(target)
    for row in document.get("detail_rows") or []:
        if row.get("row_kind") == "Diferencia documentada":
            continue
        row.update(linked_balance(row.get("amount_usd"), by_row[row.get("name")], row.get("matched_targets"),
            confirmed.get(row.get("name")) if confirmed is not None else None))
        row.client_credit_usd = credits.get(row.get("name"), 0)
        row.company_credit_usd = company_credits.get(row.get("name"), 0)
        row.pending_usd = money_float(money(row.pending_usd) - money(row.client_credit_usd) - money(row.company_credit_usd))
