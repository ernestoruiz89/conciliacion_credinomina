"""Retain confirmed FIFO portions when manual targets cover a row's remainder."""
import copy
from collections import defaultdict

from credinomina_reconciliation.allocation_origin import FIFO
from credinomina_reconciliation.deposit_reconciliation import entries, stored_entries
from credinomina_reconciliation.rounding import money, money_float


def retained_instructions(remittances, rows_by_parent, registered_ids, instructions):
    manual = defaultdict(list)
    for instruction in instructions:
        if instruction.get("detail_row"):
            manual[(instruction["deposit_id"], instruction["detail_row"])].append(instruction)
    result = []
    for document in remittances:
        deposit_id = registered_ids.get(document.name)
        relevant = {row.name: row for row in rows_by_parent.get(document.name, [])
                    if (deposit_id, row.name) in manual}
        if not relevant or not document.get("allocation_detail"):
            continue
        grouped = defaultdict(list)
        for entry in stored_entries(document):
            if (entry.get("tipo") == "Aplicacion historica" and entry.get("origen") == FIFO
                    and entry.get("fila_detalle") in relevant and not entry.get("reclassification_operation_id")):
                grouped[(entry["fila_detalle"], "H:" + str(entry.get("aplicacion_id") or ""))].append(entry)
        for (row_name, claim), saved in grouped.items():
            requested = manual[(deposit_id, row_name)]
            # Overlapping claims, signed groups and ambiguous old portions keep
            # their existing validation/undo path; never infer a redistribution.
            if (len(saved) != 1 or any(not item["claim_id"].startswith("H:") or money(item["amount_usd"]) <= 0
                                      for item in requested) or claim in {item["claim_id"] for item in requested}):
                continue
            entry = saved[0]
            amount = money(entry["importe_usd"])
            matches = [part for part in entries(relevant[row_name].get("matched_targets")) if part.get("claim_id") == claim]
            parts = entry.get("aplicaciones_fifo")
            if (amount <= 0 or len(matches) != 1 or money(matches[0].get("amount_usd")) != amount
                    or not isinstance(parts, list) or not parts
                    or any(not isinstance(part, dict) or part.get("application_id") != claim[2:]
                           or money(part.get("amount_usd")) <= 0 for part in parts)
                    or sum((money(part["amount_usd"]) for part in parts), money(0)) != amount):
                continue
            result.append(dict(id=f"RETAINED:{document.name}:{row_name}:{claim}", deposit_id=deposit_id,
                claim_id=claim, amount_usd=money_float(amount), origin=FIFO, detail_row=row_name,
                fifo_applications=copy.deepcopy(parts), **({"group": entry["empresa"]} if entry.get("empresa") else {})))
    return result
