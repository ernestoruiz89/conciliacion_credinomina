"""Read-only control of cash by receipt month, independently of payroll month."""
import json

import frappe

from credinomina_reconciliation.rounding import money, money_float


def _entries(value):
    try:
        rows = json.loads(value or "[]") if isinstance(value, str) else value or []
    except (TypeError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def get_cash_deposits(year, employer=None):
    """Do not filter by assignment date or require a period in the receipt month."""
    if not frappe.has_permission("CN Remittance Allocation", "read"):
        return None  # Unavailable is not zero.
    filters = {"docstatus": 1}
    if year is not None:
        filters["deposit_date"] = ["between", [f"{year}-01-01", f"{year}-12-31"]]
    if employer:
        filters["employer"] = employer
    deposits = frappe.get_list(
        "CN Remittance Allocation", filters=filters,
        fields=["name", "employer", "bank_account", "deposit_reference", "deposit_date",
                "deposit_currency", "deposit_amount", "amount_usd", "allocated_usd",
                "justified_surplus_usd", "allocation_detail", "result"],
        order_by="deposit_date asc, name asc", limit_page_length=0,
    )
    # Parent permission checks also apply to descriptions of complementary items
    # and periods; never expose an inaccessible document through the overview.
    item_ids = sorted({e["partida"] for d in deposits
                       for e in _entries(d.get("allocation_detail")) if e.get("partida")})
    items = {}
    if frappe.has_permission("CN Complementary Item", "read"):
        for offset in range(0, len(item_ids), 500):
            for item in frappe.get_list(
                "CN Complementary Item", filters={"name": ["in", item_ids[offset:offset + 500]], "docstatus": 1},
                fields=["name", "period", "category"], limit_page_length=0,
            ):
                items[item["name"]] = item
    period_ids = sorted({e.get("periodo") or items.get(e.get("partida"), {}).get("period")
                         for d in deposits for e in _entries(d.get("allocation_detail"))} - {None, ""})
    periods = {}
    if frappe.has_permission("CN Reconciliation Period", "read"):
        for offset in range(0, len(period_ids), 500):
            for period in frappe.get_list(
                "CN Reconciliation Period", filters={"name": ["in", period_ids[offset:offset + 500]]},
                fields=["name", "payroll_month"], limit_page_length=0,
            ):
                periods[period["name"]] = period
    return build_cash_deposits(deposits, items, periods)


def build_cash_deposits(deposits, items=None, periods=None):
    """Actual allocations classify cash; planned/manual targets do not settle it."""
    items, periods = items or {}, periods or {}
    output = []
    for deposit in {d["name"]: d for d in deposits}.values():
        credits, other, adjustments = money(0), money(0), money(0)
        related, months = set(), set()
        destinations = {}
        for entry in _entries(deposit.get("allocation_detail")):
            period = entry.get("periodo") or items.get(entry.get("partida"), {}).get("period")
            if period:
                related.add(period)
                if periods.get(period, {}).get("payroll_month"):
                    months.add(str(periods[period]["payroll_month"])[:7])
            amount = money(entry.get("importe_usd"))
            visible_period = periods.get(period, {})
            month = str(visible_period.get("payroll_month") or "")[:7]
            label = visible_period.get("name") or "Período no disponible"
            kind = None
            if entry.get("tipo") in {"Cobranza", "Aplicacion historica"}:
                credits += amount
                kind = "Créditos"
            elif entry.get("tipo") == "Partida complementaria":
                other += amount
                kind = "Partida complementaria"
                item = items.get(entry.get("partida"), {})
                label = " · ".join(filter(None, [item.get("name"), item.get("category")])) or "Partida complementaria (detalle no disponible)"
            elif entry.get("tipo") == "Movimiento de conciliación":
                adjustments += amount  # Cash consumed, not the signed adjustment.
                kind = "Ajuste de conciliación"
            if kind and amount:
                key = (kind, label, month)
                destinations[key] = destinations.get(key, money(0)) + amount
        total = money(deposit.get("amount_usd"))
        allocated = money(deposit.get("allocated_usd"))
        credit_balance = money(deposit.get("justified_surplus_usd"))
        review = allocated - credits - other - adjustments
        unclassified = total - allocated - credit_balance
        result = deposit.get("result") or "Pendiente"
        needs_review = bool(
            review or unclassified or credit_balance < 0
            or (result != "Conciliado" and not (
                credit_balance > 0 and result in {"Parcial con saldo a favor", "Saldo a favor documentado"}
            ))
        )
        output.append({
            "name": deposit["name"], "employer": deposit.get("employer"),
            "bank_account": deposit.get("bank_account"),
            "reference": deposit.get("deposit_reference"),
            "date": str(deposit.get("deposit_date") or "")[:10],
            "month": str(deposit.get("deposit_date") or "")[:7],
            "currency": deposit.get("deposit_currency"),
            "original_amount": money_float(deposit.get("deposit_amount")),
            "total_usd": float(total), "credits_usd": float(credits), "other_usd": float(other),
            "adjustments_usd": float(adjustments), "credit_balance_usd": float(credit_balance),
            "unclassified_usd": float(unclassified), "review_usd": float(review),
            "result": result, "needs_review": needs_review,
            "settled": not needs_review and credit_balance == 0 and result == "Conciliado",
            "shared": len(related) > 1, "payroll_months": sorted(months),
            "destinations": [{"type": kind, "label": label, "month": month, "amount_usd": float(amount)}
                             for (kind, label, month), amount in destinations.items()],
        })
    return output
