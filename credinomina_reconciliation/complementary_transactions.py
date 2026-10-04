"""Read-only transaction counts and progress for complementary items."""
import frappe

from credinomina_reconciliation.complementary_balances import CASH_CATEGORIES, FIELDS, financial_balance, load_balances
from credinomina_reconciliation.rounding import money, money_float

KINDS = {
    "Partidas complementarias contables": True,
    "Partidas complementarias sin origen contable": False,
}


def is_active(item):
    # Direct offsets and verified accounting evidence intentionally remain docstatus=0.
    return (item.get("docstatus") == 1 or bool(item.get("registration_exception"))
            or (item.get("category") == "Compensación entre partidas" and money(item.get("compensated_usd")) > 0)
            or (item.get("review_action") == "No conciliatoria" and item.get("review_status") == "No conciliatoria"))


def progress(item, balance):
    original = abs(money(item.get("amount_usd")))
    if not is_active(item):
        return {"state": "Pendiente", "original_usd": money_float(original), "resolved_usd": 0,
                "pending_usd": money_float(original), "status_detail": "Borrador · " + (item.get("review_status") or "Pendiente")}
    pending = balance.get("pending_usd")
    if pending is None or original <= 0:
        state, resolved = "Pendiente", None
    else:
        remaining = money(pending)
        resolved = min(original, max(original - remaining, money(0)))
        settled = balance["financial_status"] in {"Conciliada", "Documentado", "Vigente", "Registro contable verificado", "No conciliatoria"}
        state = "Conciliado" if remaining == 0 and settled else "Parcial" if resolved > 0 else "Pendiente"
    return {"state": state, "original_usd": money_float(original),
            "resolved_usd": None if resolved is None else money_float(resolved),
            "pending_usd": pending, "status_detail": balance["financial_status"]}


def load_transactions(kind, drafts, scope, dates, detail=False):
    from_accounting = KINDS[kind]
    date_field = "source_date" if from_accounting else "posting_date"
    fields = list(dict.fromkeys([*FIELDS, "accounting_source_key", "source_date", "client_name", "client_number", "loan_number",
        "source_client_name", "reference", "voucher", "source_voucher", "accounting_reference"]
        + (["source_description", "review_notes"] if detail else [])))
    items = frappe.get_list("CN Complementary Item", filters={**scope, "docstatus": ["!=", 2],
        "accounting_source_key": ["is", "set" if from_accounting else "not set"], date_field: ["between", dates]},
        fields=fields, limit_page_length=0)
    items = [item for item in items if (drafts or is_active(item))
             and not (item.get("category") == "Diferencia por tolerancia" and item.get("status") == "Revertido")]
    # Only financially active items can use confirmed cash distribution.
    active = [item for item in items if is_active(item)]
    cash_items = [item for item in active if item.get("category") in CASH_CATEGORIES
                  and not item.get("registration_exception") and item.get("review_action") != "No conciliatoria"]
    balances = {item.name: financial_balance(item) for item in active}
    if cash_items:
        if frappe.has_permission("CN Remittance Allocation", "read"):
            balances.update(load_balances(cash_items))
        else:
            for item in cash_items:
                balances[item.name].update(pending_usd=None, used_usd=None, financial_status="Distribución no disponible por permisos")
    records = []
    for item in items:
        balance = balances.get(item.name) or financial_balance(item)
        item["_progress"] = progress(item, balance)
        item["_balance"] = balance
        item["event_date"] = item.get(date_field)
        item["client_name"] = item.get("client_name") or item.get("source_client_name") or ""
        records.append({"name": item.name, "employer": item.get("employer"), "date": item["event_date"], "state": item["_progress"]["state"]})
    return records, items, {}


def month_amounts(items):
    amounts = {}
    for item in items:
        key = (item.get("employer") or "", str(item.get("event_date") or "")[:7])
        total = amounts.setdefault(key, {"total_usd": money(0), "covered_usd": money(0)})
        row = item["_progress"]
        if row["resolved_usd"] is None or row["original_usd"] <= 0 or total["total_usd"] is None:
            total.update(total_usd=None, covered_usd=None)
        else:
            total["total_usd"] += money(row["original_usd"])
            total["covered_usd"] += money(row["resolved_usd"])
    return amounts
