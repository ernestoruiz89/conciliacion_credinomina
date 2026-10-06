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


def get_cash_deposits(year, employer=None, *, include_details=True, deposit_name=None, deposits=None):
    """Do not filter by assignment date or require a period in the receipt month."""
    if not frappe.has_permission("CN Remittance Allocation", "read"):
        return None  # Unavailable is not zero.
    filters = {"docstatus": 1}
    if year is not None:
        filters["deposit_date"] = ["between", [f"{year}-01-01", f"{year}-12-31"]]
    if employer:
        filters["employer"] = employer
    if deposit_name:
        filters["name"] = deposit_name
    deposits = deposits if deposits is not None else frappe.get_list(
        "CN Remittance Allocation", filters=filters,
        fields=["name", "employer", "bank_account", "deposit_reference", "deposit_date",
                "deposit_currency", "deposit_amount", "fx_rate", "amount_usd", "allocated_usd",
                "justified_surplus_usd", "allocation_detail", "result"],
        order_by="deposit_date asc, name asc", limit_page_length=0,
    )
    # Parent permission checks also apply to descriptions of complementary items
    # and periods; never expose an inaccessible document through the overview.
    item_ids = sorted({e.get("partida") or e.get("movimiento") for d in deposits
                       for e in _entries(d.get("allocation_detail")) if e.get("partida") or e.get("movimiento")})
    items = {}
    client_credits = []
    if frappe.has_permission("CN Complementary Item", "read"):
        names = [deposit["name"] for deposit in deposits]
        for offset in range(0, len(names), 500):
            client_credits.extend(frappe.get_list("CN Complementary Item", filters={
                "registered_deposit": ["in", names[offset:offset + 500]], "docstatus": 1,
                "category": ["in", ["Saldo a favor del cliente", "Saldo a favor de la empresa"]], "result": "Saldo a favor documentado",
            }, fields=["name", "category", "registered_deposit", "employer", "client_name", "client_number", "loan_number", "amount_usd",
                       "credit_pending_usd", "credit_management_status"], limit_page_length=0))
        for offset in range(0, len(item_ids), 500):
            for item in frappe.get_list(
                "CN Complementary Item", filters={"name": ["in", item_ids[offset:offset + 500]], "docstatus": 1},
                fields=["name", "period", "category", "employer", "subcategory_effect"], limit_page_length=0,
            ):
                items[item["name"]] = item
    period_ids = sorted({e.get("periodo") or items.get(e.get("partida"), {}).get("period")
                         for d in deposits for e in _entries(d.get("allocation_detail"))} - {None, ""})
    periods = {}
    if frappe.has_permission("CN Reconciliation Period", "read"):
        for offset in range(0, len(period_ids), 500):
            for period in frappe.get_list(
                "CN Reconciliation Period", filters={"name": ["in", period_ids[offset:offset + 500]]},
                fields=["name", "payroll_month", "employer"], limit_page_length=0,
            ):
                periods[period["name"]] = period
    receivables = []
    receivable_names = [name for name, item in items.items() if item.get('subcategory_effect') == 'CxC a la empresa']
    if receivable_names:
        from credinomina_reconciliation.deposit_adjustment_receivables import load_receivables
        receivables = load_receivables(item_names=receivable_names)
    if not include_details:
        return build_cash_deposits(deposits, items, periods, include_details=False, client_credits=client_credits, receivables=receivables)
    people = _load_credit_people(deposits, periods)
    return build_cash_deposits(deposits, items, periods, people, client_credits=client_credits, receivables=receivables)


def _credit_key(entry):
    if entry.get("tipo") == "Aplicacion historica":
        return ("H", entry.get("periodo"), entry.get("aplicacion_id"))
    return ("C", entry.get("periodo"), entry.get("fila_id"))


def _load_credit_people(deposits, periods):
    """Resolve child identities in batches, honoring their parent permissions."""
    wanted = {_credit_key(e) for d in deposits for e in _entries(d.get("allocation_detail"))
              if e.get("tipo") in {"Cobranza", "Aplicacion historica"}
              and e.get("periodo") in periods}
    result = {}
    fields = ["client_name", "client_number", "loan_number"]
    historical_ids = sorted({key[2] for key in wanted if key[0] == "H" and key[2]})
    if historical_ids and frappe.has_permission("CN Accounting Import", "read"):
        for offset in range(0, len(historical_ids), 500):
            rows = frappe.get_all("CN Source Row", filters={
                "name": ["in", historical_ids[offset:offset + 500]],
                "parenttype": "CN Accounting Import", "parentfield": "rows",
            }, fields=["name", "parent", "historical_period", *fields], limit_page_length=0)
            parents = sorted({r["parent"] for r in rows})
            allowed = set(frappe.get_list("CN Accounting Import", filters={"name": ["in", parents]},
                                         pluck="name", limit_page_length=0)) if parents else set()
            for row in rows:
                key = ("H", row.get("historical_period"), row["name"])
                if row["parent"] in allowed and key in wanted:
                    result[key] = {field: row.get(field) for field in fields}
    collection_ids = sorted({key[2] for key in wanted if key[0] == "C" and key[2]})
    for offset in range(0, len(collection_ids), 500):
        for row in frappe.get_all("CN Collection Row", filters={
            "row_key": ["in", collection_ids[offset:offset + 500]],
            "parent": ["in", sorted(periods)], "parenttype": "CN Reconciliation Period",
            "parentfield": "collection_rows",
        }, fields=["row_key", "parent", *fields], limit_page_length=0):
            key = ("C", row["parent"], row["row_key"])
            if key in wanted:
                result[key] = {field: row.get(field) for field in fields}
    return result


def build_cash_deposits(deposits, items=None, periods=None, people=None, *, include_details=True, client_credits=None, receivables=()):
    """Actual allocations classify cash; planned/manual targets do not settle it."""
    items, periods, people = items or {}, periods or {}, people or {}
    output = []
    for deposit in {d["name"]: d for d in deposits}.values():
        credits, other, adjustments = money(0), money(0), money(0)
        related, months = set(), set()
        destinations = {}
        destination_employers = {}
        credit_details = {}
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
                if entry.get("fila_detalle") and entry.get("empresa"):
                    label += " · " + entry["empresa"]
            elif entry.get("tipo") == "Movimiento de conciliación":
                adjustments += amount  # Cash consumed, not the signed adjustment.
                kind = "Partida complementaria"
                item = items.get(entry.get('movimiento'), {})
                label = ' · '.join(filter(None, [item.get('name'), item.get('category')])) or 'Partida complementaria (detalle no disponible)'
            if include_details and kind and amount:
                key = (kind, label, month)
                destination_employers[key] = entry.get("empresa") or visible_period.get("employer") or items.get(entry.get("partida"), {}).get("employer") or ""
                destinations[key] = destinations.get(key, money(0)) + amount
                if kind == "Créditos" or (kind == "Partida complementaria" and entry.get("fila_detalle")):
                    person = (people.get(_credit_key(entry), {}) if kind == "Créditos" else
                              {"client_name": entry.get("cliente"), "client_number": entry.get("nro_cliente"), "loan_number": entry.get("credito")})
                    # Never merge different unknown clients or different credits.
                    identity = (person.get("client_number") or person.get("client_name")
                                or _credit_key(entry), person.get("loan_number") or "")
                    details = credit_details.setdefault(key, {})
                    detail = details.setdefault(identity, {
                        "client_name": person.get("client_name") or "Cliente no disponible",
                        "client_number": person.get("client_number") or "",
                        "loan_number": person.get("loan_number") or "", "amount_usd": money(0),
                    })
                    detail["amount_usd"] += amount
        total = money(deposit.get("amount_usd"))
        allocated = money(deposit.get("allocated_usd"))
        credit_balance = money(deposit.get("justified_surplus_usd"))
        balances = [item for item in client_credits or [] if item.get("registered_deposit") == deposit["name"]]
        client_balances = [item for item in balances if item.get("category") != "Saldo a favor de la empresa"]
        company_balances = [item for item in balances if item.get("category") == "Saldo a favor de la empresa"]
        client_total = sum((money(item.get("amount_usd")) for item in client_balances), money(0))
        def pending(items):
            return sum((money(item.get("credit_pending_usd") if item.get("credit_management_status")
                              else item.get("amount_usd")) for item in items), money(0))
        client_pending = pending(client_balances)
        company_total = sum((money(item.get("amount_usd")) for item in company_balances), money(0))
        company_pending = pending(company_balances)
        review = allocated - credits - other - adjustments
        unclassified = total - allocated - credit_balance
        result = deposit.get("result") or "Pendiente"
        needs_review = bool(
            review or unclassified or credit_balance < 0
            or (result not in {"Conciliado", "Conciliado con saldo a favor del cliente"} and not (
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
            "fx_rate": deposit.get("fx_rate"),
            "original_amount": money_float(deposit.get("deposit_amount")),
            "total_usd": float(total), "credits_usd": float(credits), "other_usd": float(other),
            "adjustments_usd": float(adjustments), "credit_balance_usd": float(credit_balance),
            "client_credit_usd": float(client_total), "client_credit_pending_usd": float(client_pending),
            "company_credit_usd": float(company_total), "company_credit_pending_usd": float(company_pending),
            "credit_management_pending_usd": float(client_pending + company_pending),
            "receivable_entries": [dict(name=item['name'], employer=item.get('employer'), amount_usd=item['receivable_usd'])
                for item in receivables if deposit['name'] in (item.get('related_deposits') or '').split(', ')],
            "undetailed_credit_usd": float(credit_balance - client_total - company_total),
            "unclassified_usd": float(unclassified), "review_usd": float(review),
            "result": result, "needs_review": needs_review,
            "settled": not needs_review and credit_balance == client_total + company_total,
            "shared": len(related) > 1, "payroll_months": sorted(months),
            "destinations": [{"type": kind, "label": label, "month": month, "amount_usd": float(amount),
                              "employer": destination_employers.get((kind, label, month), ""),
                              "people": [{**person, "amount_usd": float(person["amount_usd"])}
                                         for person in credit_details.get((kind, label, month), {}).values()]}
                             for (kind, label, month), amount in destinations.items()],
        })
        if include_details:
            output[-1]["destinations"].extend({"type": item.get("category") or "Saldo a favor del cliente", "label": item.get("name"),
                "management_status": item.get("credit_management_status") or "Pendiente",
                "month": "", "employer": item.get("employer"), "amount_usd": float(money(item.get("amount_usd"))),
                "people": [{"client_name": item.get("client_name"), "client_number": item.get("client_number"), "loan_number": item.get("loan_number"),
                            "amount_usd": float(money(item.get("amount_usd")))}] if item in client_balances else []} for item in balances)
        if not include_details:
            output[-1].pop("destinations")
            output[-1]["detail_loaded"] = False
    return output
