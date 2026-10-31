"""Read-only navigation from applications to their executed cash assignments."""
import json
from collections import defaultdict

import frappe

from credinomina_reconciliation.accounting_client_summary import collection_links
from credinomina_reconciliation.report_records import PAGE_SIZE, records
from credinomina_reconciliation.rounding import money


def _entries(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return []
    return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []


def _collection_sources(sources):
    """Legacy cash assigned to a collection is a shared link, not a cash split."""
    by_collection = defaultdict(set)
    for source in sources:
        if source.get("historical_period"):
            continue  # Direct applications must use their own cash journal.
        net = source.get("net_applied_usd")
        if net is None:
            net = money(source.get("amount_usd")) - money(source.get("application_adjustment_usd"))
        for link in collection_links(source, net):
            if link.get("collection_row_id") and money(link.get("amount_usd")) > 0:
                by_collection[link["collection_row_id"]].add(source["name"])
    if not by_collection or not frappe.has_permission("CN Reconciliation Period", "read"):
        return {}
    collections = []
    names = sorted(by_collection)
    for offset in range(0, len(names), PAGE_SIZE):
        collections.extend(frappe.get_all("CN Collection Row", filters={
            "name": ["in", names[offset:offset + PAGE_SIZE]],
            "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows",
        }, fields=["name", "parent", "row_key"], limit_page_length=0))
    parents = sorted({row.parent for row in collections})
    readable = set()
    for offset in range(0, len(parents), PAGE_SIZE):
        readable.update(row.name for row in records("CN Reconciliation Period", filters={
            "name": ["in", parents[offset:offset + PAGE_SIZE]], "docstatus": ["!=", 2],
        }, fields=["name"]))
    result = defaultdict(set)
    for row in collections:
        if row.parent in readable and row.row_key:
            result[(row.parent, row.row_key)].update(by_collection[row.name])
    return result


def load_links(sources, *, include_amounts=False):
    if not sources or not frappe.has_permission("CN Remittance Allocation", "read"):
        return {}
    names = {source["name"] for source in sources}
    collections = _collection_sources(sources)
    # Narrow the readable parent queries by journal identifiers, then verify
    # exact executed targets below. LIKE alone never establishes a link.
    needles = sorted(names | {key[1] for key in collections})
    deposits = {}
    for offset in range(0, len(needles), PAGE_SIZE):
        for deposit in records("CN Remittance Allocation", filters={"docstatus": 1},
                or_filters=[["allocation_detail", "like", "%" + value + "%"]
                            for value in needles[offset:offset + PAGE_SIZE]],
                fields=["name", "allocation_detail", "deposit_date", "deposit_reference", "cash_identity_status"]):
            deposits[deposit.name] = deposit
    result = defaultdict(dict)
    for deposit in deposits.values():
        if deposit.get("cash_identity_status") == "Origen vinculado":
            continue
        for entry in _entries(deposit.get("allocation_detail")):
            if money(entry.get("importe_usd")) <= 0:
                continue
            direct = entry.get("aplicacion_id") or entry.get("historical_application")
            targets = {direct} & names if direct else set()
            amounts = {direct: money(entry.get("importe_usd"))} if direct else {}
            via_collection = False
            if not direct and not (entry.get("partida") or entry.get("complementary_item")):
                if entry.get("aplicaciones_fifo"):
                    for part in _entries(entry["aplicaciones_fifo"]):
                        if money(part.get("amount_usd")) > 0:
                            key = part.get("application_id")
                            amounts[key] = amounts.get(key, money(0)) + money(part["amount_usd"])
                    targets = set(amounts) & names
                else:
                    targets = collections.get((entry.get("periodo"), entry.get("fila_id")), set())
                    via_collection = True
            for name in targets:
                previous = result[name].get(deposit.name)
                result[name][deposit.name] = dict(document=deposit.name,
                    date=str(deposit.get("deposit_date") or ""), reference=deposit.get("deposit_reference") or "",
                    via_collection=via_collection and (previous is None or previous["via_collection"]))
                if include_amounts:
                    amount = None if via_collection else amounts[name]
                    previous_amount = previous.get("amount_usd", 0) if previous else 0
                    result[name][deposit.name]["amount_usd"] = (
                        None if amount is None or previous_amount is None else float(money(previous_amount) + amount))
    return {name: sorted(links.values(), key=lambda row: (row["date"], row["document"]))
            for name, links in result.items()}
