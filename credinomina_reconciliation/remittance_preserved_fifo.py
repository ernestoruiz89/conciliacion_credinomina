"""Preserve confirmed automatic cash before planning a remaining distribution."""
import copy
from collections import defaultdict

from credinomina_reconciliation.allocation_origin import DETAIL, FIFO
from credinomina_reconciliation.deposit_reconciliation import entries, stored_entries
from credinomina_reconciliation.rounding import money, money_float


def retained_detail_instructions(remittances, rows_by_parent, registered_ids, instructions, claims, allowed_by_parent,
                                 *, claim_index=None):
    """Reserve confirmed detail cash before FIFO can exhaust the deposit.

    Replay fully covered historical rows and partial historical rows whose
    shortfall is documented at the company level. Both journals must agree.
    Changed identities and explicit replacements keep their usual validation.
    """
    from credinomina_reconciliation.company_conversion import KIND as COMPANY_DIFFERENCE
    from credinomina_reconciliation.deposit_difference import basis, is_difference
    from credinomina_reconciliation.remittance_detail import _candidate_matches, detail_amount_usd
    from credinomina_reconciliation.remittance_detail_status import review_status
    from credinomina_reconciliation.remittance_periods import selected_periods

    by_id = claim_index.by_id if claim_index is not None else {claim['id']: claim for claim in claims}
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
        rows = rows_by_parent.get(document.name, [])
        differences = [row for row in rows if is_difference(row)]
        origins = {DETAIL}
        documented_shortfall = money(0)
        if (len(differences) == 1 and money(differences[0].get("difference_amount_usd")) < 0
                and differences[0].get("difference_reason") and not differences[0].get("difference_core_item")):
            origins.add(FIFO)
            if differences[0].get("difference_basis") == basis(document, rows):
                documented_shortfall = -money(differences[0]["difference_amount_usd"])
        for row in rows:
            if (deposit_id, row.name) in explicit:
                continue
            saved = grouped.get(row.name, [])
            amount, _reason = detail_amount_usd(row, float(document.get('fx_rate') or 0))
            linked = money(row.get('linked_usd'))
            shortfall = money(amount) - linked
            status = review_status(row.get('match_status'))
            paid = not shortfall and status == 'Conciliada'
            company_partial = (0 < shortfall <= documented_shortfall
                and status == 'Revisar: Cobertura parcial; diferencia de empresa'
                and money(row.get('pending_usd')) == shortfall
                and not (row.get('client_credit_usd') or row.get('company_credit_usd')))
            if (not saved or linked <= 0 or not (paid or company_partial)
                    or sum((money(entry['importe_usd']) for entry in saved), money(0)) != linked):
                continue
            targets, fifo_parts, valid = {}, defaultdict(list), True
            for entry in saved:
                claim_id = 'H:' + str(entry.get('aplicacion_id') or '')
                claim = by_id.get(claim_id)
                origin = entry.get('origen')
                parts = entry.get('aplicaciones_fifo')
                if (entry.get('tipo') != 'Aplicacion historica' or origin not in origins
                        or company_partial and origin != FIFO
                        or entry.get('reclassification_operation_id')
                        or money(entry['importe_usd']) <= 0
                        or (deposit_id, claim_id) in requested or not claim or claim.get('kind') != 'H'
                        or claim.get('period') not in periods
                        or claim.get('group') not in allowed_by_parent.get(document.name, [])
                        or entry.get('empresa') and entry['empresa'] != claim.get('group')
                        or not _candidate_matches(row, claim)):
                    valid = False
                    break
                if origin == FIFO:
                    if not _proven_fifo_parts(entry, parts):
                        valid = False
                        break
                    fifo_parts[claim_id].extend(parts)
                elif parts:
                    valid = False
                    break
                targets[claim_id] = targets.get(claim_id, money(0)) + money(entry['importe_usd'])
            cache = entries(row.get('matched_targets'))
            if (not valid or len(cache) != len(targets) or any(
                    part.get('claim_id') not in targets or money(part.get('amount_usd')) != targets[part['claim_id']]
                    or (part.get('fifo_applications') or []) != fifo_parts.get(part['claim_id'], [])
                    for part in cache) or {part.get('claim_id') for part in cache} != set(targets)):
                continue
            if company_partial:
                documented_shortfall -= shortfall
            for index, entry in enumerate(saved):
                origin = entry['origen']
                result.append(dict(id=f'RETAINED:{document.name}:DETAIL:{row.name}:{index}', deposit_id=deposit_id,
                    claim_id='H:' + entry['aplicacion_id'], amount_usd=money_float(entry['importe_usd']),
                    origin=origin, detail_row=row.name, retained_detail=True,
                    **({'retained_company_shortfall': True} if company_partial else {}),
                    **({'fifo_applications': copy.deepcopy(entry['aplicaciones_fifo'])} if origin == FIFO else {}),
                    **({'group': entry['empresa']} if entry.get('empresa') else {})))
    return result


def _proven_fifo_parts(entry, parts):
    return (isinstance(parts, list) and bool(parts)
            and all(isinstance(part, dict) and part.get("application_id") == entry["aplicacion_id"]
                    and money(part.get("amount_usd")) > 0 for part in parts)
            and sum((money(part["amount_usd"]) for part in parts), money(0)) == money(entry["importe_usd"]))


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
                    or not _proven_fifo_parts(entry, parts)):
                continue
            result.append(dict(id=f"RETAINED:{document.name}:{row_name}:{claim}", deposit_id=deposit_id,
                claim_id=claim, amount_usd=money_float(amount), origin=FIFO, detail_row=row_name,
                fifo_applications=copy.deepcopy(parts), **({"group": entry["empresa"]} if entry.get("empresa") else {})))
    return result
