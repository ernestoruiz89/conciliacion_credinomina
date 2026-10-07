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
    credits = {}
    company_credits = {}
    if document.get("doctype") == "CN Remittance Allocation" and document.get("name") and document.get("docstatus") == 1:
        from credinomina_reconciliation.client_credit import load_credits, row_credit_amounts, RESULT
        items = [item for item in load_credits([document.name]) if item.result == RESULT]
        credits = row_credit_amounts([item for item in items if item.category == "Saldo a favor del cliente"], document.get("detail_rows") or [])
        company_credits = row_credit_amounts([item for item in items if item.category == "Saldo a favor de la empresa"], document.get("detail_rows") or [])
    by_row = defaultdict(list)
    for target in document.get("targets") or []:
        if target.get("detail_row"):
            by_row[target.get("detail_row")].append(target)
    for row in document.get("detail_rows") or []:
        row.update(linked_balance(row.get("amount_usd"), by_row[row.get("name")], row.get("matched_targets")))
        row.client_credit_usd = credits.get(row.get("name"), 0)
        row.company_credit_usd = company_credits.get(row.get("name"), 0)
        row.pending_usd = money_float(money(row.pending_usd) - money(row.client_credit_usd) - money(row.company_credit_usd))
