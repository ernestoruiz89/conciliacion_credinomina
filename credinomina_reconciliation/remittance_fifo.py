"""Explicit, date-ordered application distribution; no cash or identity guesses."""
from datetime import date

from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.rounding import money, money_float


def fifo_targets(matches, amount_usd, reserved=None):
    """Allocate available dated capacity, leaving any detail excess pending.

    Claims are already scoped and identity-checked by remittance_detail. An
    operative claim can contain several applications: sort their dated slices,
    not the payroll month or the collection row's creation date. Existing cash
    is deducted from the exact recorded application slices. Legacy cash without
    a slice cannot choose a different application by assuming oldest-first.
    """
    reserved = reserved or {}
    loans = {canonical_credit_number(claim.get("loan_number")) for claim in matches}
    people = {clean_text(claim.get("client")) or canonical_identifier(claim.get("client_number"))
              for claim in matches}
    if len(loans) != 1 or "" in loans or len(people) != 1 or "" in people:
        return [], "FIFO requiere un mismo cliente y crédito identificados; revise los destinos"
    slices = []
    for claim in matches:
        used = max(money(reserved.get(claim["id"])), money(0))
        available = max(money(claim["amount_usd"]) - used, money(0))
        if claim.get("kind") == "C":
            available = min(available, max(money(claim.get("core_applied_usd")) - used, money(0)))
        if not available:
            continue
        parts = claim.get("fifo_applications") or []
        if not parts:
            return [], "FIFO requiere la fecha y el importe de las aplicaciones vinculadas"
        dated = []
        for part in parts:
            if money(part.get("amount_usd")) <= 0:
                continue
            try:
                day = date.fromisoformat(str(part.get("event_date") or "")[:10])
            except ValueError:
                return [], "FIFO requiere una fecha válida en todas las aplicaciones candidatas"
            dated.append((day, str(part["id"]), money(part["amount_usd"])))
        covered = max(money(claim.get("fifo_covered_usd")), money(0))
        specific = claim.get("fifo_covered_by_application") or {}
        dated_totals = {}
        for day, application_id, amount in dated:
            key = (day, application_id)
            dated_totals[key] = dated_totals.get(key, money(0)) + amount
        dated = [(day, application_id, amount) for (day, application_id), amount in dated_totals.items()]
        capacities = {application_id: amount for _day, application_id, amount in dated}
        if any(application_id not in capacities or money(amount) < 0 or money(amount) > capacities[application_id]
               for application_id, amount in specific.items()):
            return [], "La cobertura guardada no coincide con las aplicaciones vigentes; revise la distribución antes de aplicar FIFO"
        if claim.get("kind") == "C" and covered and len(dated) > 1:
            if sum((money(value) for value in specific.values()), money(0)) != covered:
                return [], "La cobranza tiene depósitos previos sin reparto por aplicación; revise sus vínculos antes de aplicar FIFO"
        # Current request reservations consume remaining dated capacity in the
        # same order as this plan; earlier persisted cash keeps its exact slice.
        skip = used if specific else covered + used
        for day, application_id, amount in sorted(dated):
            if specific:
                amount = max(amount - max(money(specific.get(application_id)), money(0)), money(0))
            consumed = min(skip, amount)
            skip -= consumed
            amount = min(amount - consumed, available)
            if amount > 0:
                slices.append((day, application_id, claim["id"], amount))
                available -= amount
            if not available:
                break
    requested = money(amount_usd)
    if requested <= 0 or not slices:
        return [], "Sin importe disponible para distribuir entre las aplicaciones pendientes del cliente y crédito en los períodos seleccionados"
    targets = {}
    for day, application_id, claim_id, amount in sorted(slices):
        assigned = min(requested, amount)
        target = targets.setdefault(claim_id, {"claim_id": claim_id, "amount_usd": 0, "fifo_applications": []})
        target["amount_usd"] = money_float(money(target["amount_usd"]) + assigned)
        target["fifo_applications"].append({"application_id": application_id,
            "event_date": day.isoformat(), "amount_usd": money_float(assigned)})
        requested -= assigned
        if not requested:
            break
    reason = "Automática FIFO: aplicaciones de la más antigua a la más reciente"
    if requested:
        reason += f"; distribución parcial, pendiente de distribuir US$ {requested:.2f}"
    return list(targets.values()), reason
