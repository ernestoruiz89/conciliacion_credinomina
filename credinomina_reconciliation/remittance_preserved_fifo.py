"""Preserve confirmed automatic cash before planning a remaining distribution."""
import copy
from collections import defaultdict

from credinomina_reconciliation.allocation_origin import DETAIL, FIFO
from credinomina_reconciliation.deposit_reconciliation import entries, stored_entries
from credinomina_reconciliation.rounding import money, money_float


def retained_detail_instructions(remittances, rows_by_parent, registered_ids, instructions, claims, allowed_by_parent):
    """Switching to FIFO must not replay completed pre-FIFO detail decisions.

    Replay only the exact, fully paid historical links proved by both journals.
    Partial rows, changed identities, explicit replacements and unknown evidence
    keep their existing validation path. This never proposes a new cross-credit
    match or treats a planned target as already paid.
    """
    from credinomina_reconciliation.company_conversion import KIND as COMPANY_DIFFERENCE
    from credinomina_reconciliation.remittance_detail import _candidate_matches, detail_amount_usd
    from credinomina_reconciliation.remittance_periods import selected_periods

    by_id = {claim['id']: claim for claim in claims}
    explicit = {(entry['deposit_id'], entry.get('detail_row')) for entry in instructions}
    requested = {(entry['deposit_id'], entry['claim_id']) for entry in instructions}
    result = []
    for document in remittances:
        deposit_id = registered_ids.get(document.name)
        current_file = document.get('detail_file') or document.get('support_file')
        if (not deposit_id or not document.get('apply_fifo') or not document.get('allocation_detail')
                or not document.get('detail_hash') or not current_file
                or document.get('detail_source_file') != current_file):
            continue
        ledger = stored_entries(document)
        if any(entry.get('tipo') == COMPANY_DIFFERENCE for entry in ledger):
            continue  # Its signed company coverage must be reconstructed together.
        grouped = defaultdict(list)
        for entry in ledger:
            if entry.get('fila_detalle'):
                grouped[entry['fila_detalle']].append(entry)
        periods = set(selected_periods(document))
        if not periods:
            continue
        for row in rows_by_parent.get(document.name, []):
            if (deposit_id, row.name) in explicit:
                continue
            saved = grouped.get(row.name, [])
            amount, _reason = detail_amount_usd(row, float(document.get('fx_rate') or 0))
            if (not saved or amount <= 0 or money(row.get('linked_usd')) != money(amount)
                    or row.get('match_status') != 'Conciliada'
                    or sum((money(entry['importe_usd']) for entry in saved), money(0)) != money(amount)):
                continue
            targets, valid = {}, True
            for entry in saved:
                claim_id = 'H:' + str(entry.get('aplicacion_id') or '')
                claim = by_id.get(claim_id)
                if (entry.get('tipo') != 'Aplicacion historica' or entry.get('origen') != DETAIL
                        or entry.get('reclassification_operation_id') or entry.get('aplicaciones_fifo')
                        or money(entry['importe_usd']) <= 0
                        or (deposit_id, claim_id) in requested or not claim or claim.get('kind') != 'H'
                        or claim.get('period') not in periods
                        or claim.get('group') not in allowed_by_parent.get(document.name, [])
                        or entry.get('empresa') and entry['empresa'] != claim.get('group')
                        or not _candidate_matches(row, claim)):
                    valid = False
                    break
                targets[claim_id] = targets.get(claim_id, money(0)) + money(entry['importe_usd'])
            cache = entries(row.get('matched_targets'))
            if (not valid or len(cache) != len(targets) or any(
                    part.get('claim_id') not in targets or money(part.get('amount_usd')) != targets[part['claim_id']]
                    for part in cache) or {part.get('claim_id') for part in cache} != set(targets)):
                continue
            for index, entry in enumerate(saved):
                result.append(dict(id=f'RETAINED:{document.name}:DETAIL:{row.name}:{index}', deposit_id=deposit_id,
                    claim_id='H:' + entry['aplicacion_id'], amount_usd=money_float(entry['importe_usd']),
                    origin=DETAIL, detail_row=row.name, retained_detail=True,
                    **({'group': entry['empresa']} if entry.get('empresa') else {})))
    return result


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
            # Overlapping claims, signed row targets and ambiguous old portions keep
            # their existing validation/undo path; never infer a redistribution.
            if (len(saved) != 1 or any(not item["claim_id"].startswith(("H:", "X:")) or money(item["amount_usd"]) <= 0
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
