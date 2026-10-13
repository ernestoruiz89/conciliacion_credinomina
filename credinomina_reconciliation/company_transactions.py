"""Read-only monthly counts, using cash-reconciliation states, not collection matching."""
from collections import defaultdict
from datetime import date

from credinomina_reconciliation.rounding import money, money_float, sum_money


IMPORTED = {"Importado", "Importado con excepciones"}
from credinomina_reconciliation.deposit_presentation import SETTLED_RESULTS as SETTLED_DEPOSIT


def application_progress(row, parent, collections=None, *, collection_usage=None):
    """Current monetary coverage, shared by counts, month percentages and detail.

    Saved workflow labels do not prove payment. Use the same linked cash and
    confirmed adjustments as the import's client summary; never guess how a
    partially paid shared collection was split among individual applications.
    """
    from credinomina_reconciliation.accounting_client_summary import build_summary
    from credinomina_reconciliation.reconciliation import converted_amount

    original = converted_amount({**row, "application_adjustment_usd": 0}, "USD")
    eligible = (parent.get("status") in IMPORTED and row.get("event_type") == "Aplicacion"
                and bool(row.get("effective")) and row.get("match_status") != "Ignorado")
    adjustment = money(row.get("application_adjustment_usd")) if eligible else money(0)
    result = {"original_usd": original, "adjustment_usd": money_float(adjustment),
              "net_usd": original, "assigned_usd": 0.0, "rounding_usd": 0.0,
              "pending_usd": original, "state": "Pendiente", "observations": [],
              "total_usd": None if original is None else money(original) + max(-adjustment, 0),
              "covered_usd": 0.0}
    if not eligible:
        result["observations"] = ["Borrador o fila no efectiva para conciliación."]
        if original is None:
            result["covered_usd"] = None
        return result

    try:
        summaries = build_summary({**parent, "rows": [row]}, collections or {}, collection_usage=collection_usage)
    except (ValueError, TypeError):
        result.update(net_usd=converted_amount(row, "USD"), assigned_usd=None,
                      rounding_usd=None, pending_usd=None, state="Revisar distribución",
                      covered_usd=None, observations=["Detalle de distribución inválido; revise los vínculos de la aplicación."])
        return result
    amounts = summaries[0]
    if amounts["applied_usd"] is not None and amounts["assigned_usd"] is None:
        attributable = _fully_attributable_collection_amounts(row, amounts["applied_usd"], collections or {}, collection_usage)
        if attributable is not None:
            amounts = {**amounts, **attributable, "observations": []}
    result.update(net_usd=amounts["applied_usd"], assigned_usd=amounts["assigned_usd"],
                  rounding_usd=amounts["rounding_usd"], pending_usd=amounts["balance_usd"],
                  observations=list(amounts["observations"]))
    invalid = (original is None or money(original) <= 0 or adjustment > money(original)
               or any(amounts[field] is None for field in ("applied_usd", "assigned_usd", "rounding_usd", "balance_usd"))
               or money(amounts["assigned_usd"]) < 0 or bool(amounts["observations"]))
    if invalid:
        result.update(state="Revisar distribución", covered_usd=None)
        if adjustment > money(original) or (original is not None and money(original) <= 0) or (
                amounts["assigned_usd"] is not None and money(amounts["assigned_usd"]) < 0):
            result["observations"].append("Importes de aplicación o cobertura inconsistentes; revise la conciliación.")
        return result

    covered = result["total_usd"] - money(result["pending_usd"])
    result["covered_usd"] = money_float(min(max(covered, 0), result["total_usd"]))
    result["state"] = ("Conciliado" if not money(result["pending_usd"])
                       else "Parcial" if covered > 0 else "Pendiente")
    return result


def application_state(row, parent, collections=None):
    return application_progress(row, parent, collections)["state"]


def _fully_attributable_collection_amounts(row, net, collections, usage):
    """A wholly paid shared claim covers every linked amount, without proration.

    A partial shared payment remains unknown. Guard the total linked by the
    current query too, so several inconsistent links cannot each consume the
    whole collection or fabricate a complete reconciliation.
    """
    from credinomina_reconciliation.accounting_client_summary import collection_links, entries
    shares = defaultdict(lambda: money(0))
    for link in collection_links(row, net):
        amount = money(link.get("amount_usd"))
        if amount < 0:
            return None
        shares[link.get("collection_row_id")] += amount
    if sum_money(shares.values()) > money(net):
        return None
    assigned, rounding = money(0), money(0)
    for claim, share in shares.items():
        collection = collections.get(claim)
        if collection is None:
            return None
        applied = money(collection.get("applied_usd"))
        cash = sum_money(entry.get("importe_usd") for entry in entries(collection.get("remittance_detail"))
                         if entry.get("destino") != "Partida complementaria")
        difference = money(collection.get("rounding_adjustment_usd"))
        if share > applied or cash < 0 or (usage is not None and usage.get(claim, 0) > applied):
            return None
        if share == applied:
            assigned += cash
            rounding += difference
        elif not cash and not difference:
            continue
        elif usage is not None and cash == applied and not difference:
            assigned += share
        else:
            return None
    return {"assigned_usd": money_float(assigned), "rounding_usd": money_float(rounding),
            "balance_usd": money_float(max(money(net) + rounding - assigned, 0))}


def application_progress_index(sources, imports, collections):
    from credinomina_reconciliation.accounting_client_summary import collection_links
    from credinomina_reconciliation.reconciliation import converted_amount
    usage = defaultdict(lambda: money(0))
    operative_rows = set()
    for row in sources:
        parent = imports[row["parent"]]
        historical = (row.get("historical_period") or parent.get("historical_period")
                      or parent.get("historical_backfill") or row.get("processing_route") == "Historica")
        if (not historical and parent.get("status") in IMPORTED and row.get("effective")
                and row.get("match_status") != "Ignorado"):
            operative_rows.add(row["name"])
            try:
                for link in collection_links(row, converted_amount(row, "USD")):
                    usage[link.get("collection_row_id")] += max(money(link.get("amount_usd")), 0)
            except (ValueError, TypeError):
                continue  # The row's progress below retains the invalid-evidence warning.
    overlinked = {claim for claim, amount in usage.items()
                  if claim in collections and amount > money(collections[claim].get("applied_usd"))}
    result = {}
    for row in sources:
        current = application_progress(row, imports[row["parent"]], collections, collection_usage=usage)
        if overlinked and row["name"] in operative_rows:
            try:
                conflict = any(link.get("collection_row_id") in overlinked
                               for link in collection_links(row, current["net_usd"]))
            except (ValueError, TypeError):
                conflict = False  # Its unreadable evidence is already flagged in current.
            if conflict:
                current.update(state="Revisar distribución", assigned_usd=None, rounding_usd=None,
                               pending_usd=None, covered_usd=None, distribution_inconsistent=True)
                current["observations"].append("Los vínculos de aplicaciones superan el aplicado de la cuota; revise la distribución.")
        result[row["name"]] = current
    return result


def deposit_state(row):
    if row.get('cash_identity_status') == 'Revisar identidad':
        return 'Revisar distribución'
    if row.get("docstatus") != 1:
        return "Pendiente"
    total = money(row.get("amount_usd"))
    assigned = money(row.get("allocated_usd"))
    surplus = money(row.get("justified_surplus_usd"))
    if total <= 0 or assigned < 0 or surplus < 0 or assigned + surplus > total:
        return "Revisar distribución"
    if (total > 0 and assigned >= 0 and assigned + surplus == total and surplus >= 0
            and not money(row.get("unclassified_usd")) and row.get("result") in SETTLED_DEPOSIT
            and not str(row.get("detail_status") or '').startswith('Revisar')):
        return "Conciliado"
    return "Parcial" if assigned > 0 or surplus > 0 else "Pendiente"


def application_month_amounts(sources, imports, collections, progress=None):
    """Reuse audited cash attribution; never split shared cash across months by guesswork."""
    from credinomina_reconciliation.accounting_client_summary import build_summary
    from credinomina_reconciliation.reconciliation import converted_amount

    if progress is None:
        progress = application_progress_index(sources, imports, collections)

    groups = defaultdict(list)
    for source in sources:
        parent = imports[source["parent"]]
        groups[(source.get("report_employer", parent.get("employer")) or "", str(source.get("event_date") or "")[:7])].append(source)
    result = {}
    for key, rows in groups.items():
        total, covered, eligible_total = money(0), money(0), money(0)
        known = True
        eligible_rows = []
        row_progress = []
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
            if eligible:
                eligible_total += amount
                historical = source.get("historical_period") or parent.get("historical_period")
                eligible_rows.append({**source, "historical_period": historical,
                    "processing_route": source.get("processing_route") or ("Historica" if historical or parent.get("historical_backfill") else "Operativa")})
                row_progress.append(progress[source["name"]])
        if all(item["covered_usd"] is not None for item in row_progress):
            covered = sum_money(item["covered_usd"] for item in row_progress)
        else:
            # Individual cash may be unknown while the complete same-month
            # collection remains attributable. Keep that audited aggregate;
            # an inconsistent amount must never use this fallback.
            try:
                summaries = build_summary({"rows": eligible_rows}, collections)
            except (ValueError, TypeError):
                known = False
                summaries = []
            invalid_amount = any(item.get("distribution_inconsistent") or item["original_usd"] is None or money(item["original_usd"]) <= 0
                or money(item["adjustment_usd"]) > money(item["original_usd"])
                or (item["assigned_usd"] is not None and money(item["assigned_usd"]) < 0) for item in row_progress)
            if invalid_amount or any(item["balance_usd"] is None or item["observations"] for item in summaries):
                known = False
            else:
                covered = max(eligible_total - sum_money(item["balance_usd"] for item in summaries), 0)
        result[key] = {"total_usd": total if known else None, "covered_usd": min(covered, total) if known else None}
    return result


def deposit_month_amounts(deposits):
    result = {}
    for row in deposits:
        key = (row.get("employer") or "", str(row.get("deposit_date") or "")[:7])
        totals = result.setdefault(key, {"total_usd": money(0), "covered_usd": money(0)})
        total = money(row.get("amount_usd"))
        if total <= 0 or totals["total_usd"] is None or deposit_state(row) == "Revisar distribución":
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
            row[key + "_review"] = states.count("Revisar distribución")
            row[key + "_state"] = ("" if not states else "Conciliado" if all(value == "Conciliado" for value in states)
                else "Revisar distribución" if "Revisar distribución" in states
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
