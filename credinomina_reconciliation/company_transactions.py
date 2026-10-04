"""Read-only monthly counts, using cash-reconciliation states, not collection matching."""
from collections import defaultdict
from datetime import date

from credinomina_reconciliation.rounding import money, money_float, sum_money


IMPORTED = {"Importado", "Importado con excepciones"}
SETTLED_APPLICATION = {"Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"}
SETTLED_DEPOSIT = {"Conciliado", "Conciliado con saldo a favor del cliente", "Conciliado con saldo a favor",
                   "Saldo a favor documentado", "Parcial con saldo a favor"}


def application_state(row, parent):
    if parent.get("status") not in IMPORTED or not row.get("effective") or row.get("match_status") == "Ignorado":
        return "Pendiente"
    status = row.get("deposit_match_status")
    if status in SETTLED_APPLICATION:
        return "Conciliado"
    if status == "Depósito parcial" or money(row.get("historical_remitted_usd")) > 0:
        return "Parcial"
    # Confirmed offsets may cancel just part of an application without a deposit.
    if money(row.get("application_adjustment_usd")) > 0:
        return "Parcial"
    return "Pendiente"


def deposit_state(row):
    if row.get("docstatus") != 1:
        return "Pendiente"
    total = money(row.get("amount_usd"))
    assigned = money(row.get("allocated_usd"))
    surplus = money(row.get("justified_surplus_usd"))
    if (total > 0 and assigned >= 0 and assigned + surplus == total and surplus >= 0
            and not money(row.get("unclassified_usd")) and row.get("result") in SETTLED_DEPOSIT):
        return "Conciliado"
    return "Parcial" if assigned > 0 or surplus > 0 else "Pendiente"


def application_month_amounts(sources, imports, collections):
    """Reuse audited cash attribution; never split shared cash across months by guesswork."""
    from credinomina_reconciliation.accounting_client_summary import build_summary
    from credinomina_reconciliation.reconciliation import converted_amount

    groups = defaultdict(list)
    for source in sources:
        parent = imports[source["parent"]]
        groups[(parent.get("employer") or "", str(source.get("event_date") or "")[:7])].append(source)
    result = {}
    for key, rows in groups.items():
        total, covered, partial_total = money(0), money(0), money(0)
        known = True
        partial_rows = []
        for source in rows:
            parent = imports[source["parent"]]
            original = converted_amount({**source, "application_adjustment_usd": 0}, "USD")
            if original is None or money(original) <= 0:
                known = False
                continue
            eligible = parent.get("status") in IMPORTED and source.get("effective") and source.get("match_status") != "Ignorado"
            # Positive confirmed adjustments cancel debt; negative ones increase it.
            amount = money(original) + max(-money(source.get("application_adjustment_usd")), 0) if eligible else money(original)
            total += amount
            state = application_state(source, parent)
            if state == "Conciliado":
                covered += amount
            elif state == "Parcial":
                partial_total += amount
                historical = source.get("historical_period") or parent.get("historical_period")
                partial_rows.append({**source, "historical_period": historical,
                    "processing_route": source.get("processing_route") or ("Historica" if historical or parent.get("historical_backfill") else "Operativa")})
        summaries = build_summary({"rows": partial_rows}, collections)
        if any(item["balance_usd"] is None or item["observations"] for item in summaries):
            known = False
        else:
            covered += max(partial_total - sum_money(item["balance_usd"] for item in summaries), 0)
        result[key] = {"total_usd": total if known else None, "covered_usd": min(covered, total) if known else None}
    return result


def deposit_month_amounts(deposits):
    result = {}
    for row in deposits:
        key = (row.get("employer") or "", str(row.get("deposit_date") or "")[:7])
        totals = result.setdefault(key, {"total_usd": money(0), "covered_usd": money(0)})
        total = money(row.get("amount_usd"))
        if total <= 0 or totals["total_usd"] is None:
            totals.update(total_usd=None, covered_usd=None)
            continue
        totals["total_usd"] += total
        if row.get("docstatus") == 1:
            totals["covered_usd"] += min(total, max(money(row.get("allocated_usd")) + money(row.get("justified_surplus_usd")), 0))
    return result


def monthly_counts(records, year, amounts=None):
    """Count physical application rows / deposit documents, never deduplicate by amounts."""
    groups = defaultdict(lambda: defaultdict(list))
    for record in records:
        try:
            day = date.fromisoformat(str(record.get("date") or "")[:10])
        except ValueError:
            continue
        if day.year == year:
            groups[record.get("employer") or ""][day.month].append(record["state"])
    output = []
    for employer, months in sorted(groups.items(), key=lambda item: item[0].casefold()):
        row = {"employer": employer, "total": 0}
        for month in range(1, 13):
            key = f"m{month:02}"
            states = months.get(month, [])
            row[key] = len(states)
            row["total"] += len(states)
            row[key + "_conciliado"] = states.count("Conciliado")
            row[key + "_parcial"] = states.count("Parcial")
            row[key + "_pendiente"] = states.count("Pendiente")
            row[key + "_state"] = ("" if not states else "Conciliado" if all(value == "Conciliado" for value in states)
                else "Parcial" if any(value != "Pendiente" for value in states) else "Pendiente")
            values = (amounts or {}).get((employer, f"{year}-{month:02}"), {})
            total, covered = values.get("total_usd"), values.get("covered_usd")
            known = total is not None and covered is not None and total > 0
            row[key + "_total_usd"] = money_float(total) if known else None
            row[key + "_covered_usd"] = money_float(covered) if known else None
            row[key + "_percentage"] = float(covered * 100 / total) if known else None
            # Compare exact cents: displaying 50.00% must not round 49.999% up into yellow.
            row[key + "_half_covered"] = bool(known and covered * 2 >= total)
        output.append(row)
    return output
