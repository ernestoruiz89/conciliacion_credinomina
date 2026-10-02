"""Informational row coverage; linked destinations do not imply settlement."""
import json
from collections import defaultdict

from credinomina_reconciliation.rounding import money, money_float, sum_money


def linked_balance(amount, manual, matched):
    if isinstance(matched, str):
        try:
            matched = json.loads(matched or "[]")
        except (ValueError, TypeError):
            matched = []
    # Explicit current links supersede old suggestions. Removed manual links
    # must not be resurrected from the last reconciliation's JSON.
    if not isinstance(matched, list):
        matched = []
    entries = manual if manual else [entry for entry in matched
                                    if isinstance(entry, dict) and not entry.get("instruction_id")]
    linked = sum_money(entry.get("amount_usd") for entry in entries)
    return {"linked_usd": money_float(linked), "pending_usd": money_float(money(amount) - linked)}


def update_detail_balances(document):
    by_row = defaultdict(list)
    for target in document.get("targets") or []:
        if target.get("detail_row"):
            by_row[target.get("detail_row")].append(target)
    for row in document.get("detail_rows") or []:
        row.update(linked_balance(row.get("amount_usd"), by_row[row.get("name")], row.get("matched_targets")))
