"""Conservative per-client matching for a company's remittance detail."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.client_identity import matching_name
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money


EPSILON = 0.005


def _period_scope(period):
    return {period} if isinstance(period, str) and period else set(period or ())


def detail_amount_usd(row: Mapping[str, Any], nio_per_usd: float = 0) -> tuple[float, str]:
    """USD is authoritative when both deduction columns are populated.

    The NIO column is not added to USD: the collection template commonly
    presents the same deduction in both currencies. A NIO-only row needs a
    documented deposit FX rate, never a guessed current market rate.
    """
    usd = money_float(row.get("deducted_usd"))
    nio = money_float(row.get("deducted_nio"))
    if usd < 0 or nio < 0:
        return 0, "Importe negativo"
    if usd > EPSILON:
        return money_float(usd), "US$ informado; C$ no se suma" if nio > EPSILON else "US$ informado"
    if nio > EPSILON:
        if nio_per_usd <= 0:
            return 0, "Falta tasa C$/US$ documentada"
        return money_float(decimal_value(nio) / decimal_value(nio_per_usd)), "C$ convertido con tasa documentada"
    return 0, "No deducido"


def _same_id(left: Any, right: Any) -> bool:
    return bool(clean_text(left)) and canonical_identifier(left) == canonical_identifier(right)


def _candidate_mismatch(row: Mapping[str, Any], claim: Mapping[str, Any], *, check_reference=True):
    """Explain the same identity rules used for matching, without accepting more links."""
    if row.get("employer") and clean_text(row["employer"]) != clean_text(claim.get("group")):
        return f"La fila pertenece a {row['employer']} y el destino a {claim.get('group') or 'una empresa sin identificar'}. Revise la empresa del destino."
    if row.get("client") and claim.get("client") and row["client"] != claim["client"]:
        return f"El cliente vinculado no coincide: fila {row['client']}; destino {claim['client']}. Revise el cliente del destino."
    loan = clean_text(row.get("loan_number"))
    client = clean_text(row.get("client_number"))
    employee = clean_text(row.get("employee_number"))
    national_id = clean_text(row.get("national_id"))
    has_identifier = any((loan, client, employee, national_id))
    identity_match = bool(row.get("client") and claim.get("client") and row["client"] == claim["client"])
    if loan and canonical_credit_number(loan) != canonical_credit_number(claim.get("loan_number")):
        if not clean_text(claim.get('loan_number')):
            return f"Falta el número de crédito del destino; la fila requiere {loan}. Complete Nro. Crédito si corresponde a ese préstamo."
        return f"El crédito no coincide: fila {loan}; destino {claim['loan_number']}. Revise el crédito o seleccione otro destino."
    if not has_identifier and not clean_text(row.get("client_name")):
        return "La fila no tiene identificadores ni nombre del cliente. Identifique al cliente en el detalle."
    if client and claim.get("client_number"):
        if not _same_id(client, claim["client_number"]):
            return f"El número de cliente no coincide: fila {client}; destino {claim['client_number']}. Revise los identificadores del cliente."
        identity_match = True
    if employee and claim.get("employee_number"):
        if not _same_id(employee, claim["employee_number"]):
            return f"El número de empleado no coincide: fila {employee}; destino {claim['employee_number']}. Revise el empleado del destino."
        identity_match = True
    if national_id and claim.get("national_id"):
        if clean_text(national_id).casefold() != clean_text(claim["national_id"]).casefold():
            return "La cédula del destino no coincide con la fila. Revise la identificación del cliente."
        identity_match = True
    if (client or employee or national_id) and not identity_match and (
        not loan or claim.get("client_number") or claim.get("employee_number") or claim.get("national_id")
    ):
        return "No hay un identificador de cliente coincidente entre la fila y el destino. Complete o verifique cliente, cédula o empleado."
    # Company details often contain only the employee's name. Use an exact
    # normalized name or registered alias in that case, but never override a
    # conflicting credit/client identifier with a name match.
    if not has_identifier and not matching_name(
        row.get("client_name"), {
            "client_name": claim.get("client_name"),
            "client_aliases": claim.get("client_names") or (),
        },
    ):
        return "El nombre de la fila no coincide con el cliente ni con sus alias registrados. Verifique al cliente del detalle."
    installment = row.get("installment_number")
    if installment and claim.get("installment_number") and not _same_id(
        installment, claim.get("installment_number")
    ):
        return f"La cuota no coincide: fila {installment}; destino {claim['installment_number']}. Revise la cuota seleccionada."
    reference = clean_text(row.get("application_reference"))
    if check_reference and reference and claim.get("kind") != "X" and reference not in {
        clean_text(value) for value in claim.get("references", ())
    }:
        references = ', '.join(clean_text(value) for value in claim.get('references', ()) if clean_text(value)) or 'sin referencia'
        return f"La referencia no coincide: fila {reference}; destino {references}. Revise la referencia y la identidad antes de vincular."
    return None


def _candidate_matches(row: Mapping[str, Any], claim: Mapping[str, Any], *, check_reference=True) -> bool:
    return _candidate_mismatch(row, claim, check_reference=check_reference) is None


def _destination_label(claim):
    prefix, _, name = clean_text(claim.get('id')).partition(':')
    label = {'X': 'Partida', 'H': 'Aplicación', 'C': 'Cobranza'}.get(prefix, 'Destino')
    return f"{label} {name or claim.get('id') or 'sin identificar'}"


def _manual_application_identity_matches(row, claim, employer):
    """Only explicit application links may cross references with proven identity."""
    if claim.get("kind") != "H" or not clean_text(row.get("loan_number")):
        return False
    if clean_text(row.get("employer") or employer) != clean_text(claim.get("group")):
        return False
    same_client = (bool(row.get("client") and row.get("client") == claim.get("client"))
                   or any(_same_id(row.get(field), claim.get(field))
                          for field in ("client_number", "national_id", "employee_number")))
    return same_client and _candidate_matches(row, claim, check_reference=False)


def _unidentified_complement_link(row, claim, instruction):
    """An explicit row link may identify an otherwise unassigned complement.

    Never use this relaxation for automatic matching or to override any
    identity already recorded on a complementary item. Keep the source intact;
    the target's detail_row is the auditable attribution.
    """
    identity_fields = (
        "client", "client_number", "loan_number", "employee_number", "national_id", "client_name",
    )
    if claim.get("kind") != "X" or any(clean_text(claim.get(key)) for key in identity_fields):
        return False
    if claim.get("client_names"):
        return False
    if not row.get("name") or instruction.get("detail_row") != row.get("name"):
        return False
    if not any(clean_text(row.get(key)) for key in identity_fields):
        return False
    company = clean_text(claim.get("group"))
    if row.get("employer") and clean_text(row.get("employer")) != company:
        return False
    if instruction.get("group") and clean_text(instruction.get("group")) != company:
        return False
    if claim.get("installment_number") and not _same_id(
        row.get("installment_number"), claim.get("installment_number")
    ):
        return False
    return True


def manual_detail_targets(row, claims, instructions, amount_usd, employer, period="", allowed_groups=None,
                          allow_other_periods=False):
    """Validate explicit row links; reuse their instruction IDs without booking twice."""
    by_id = {claim["id"]: claim for claim in claims}
    periods = _period_scope(period)
    targets = []
    unidentified_complements = []
    different_references = []
    other_periods = set()
    for instruction in instructions:
        claim = by_id.get(instruction["claim_id"])
        allowed = set(allowed_groups or [employer])
        generic = bool(claim and claim.get("kind") == "X" and claim.get("manual_only"))
        groups = set(claim.get("groups") or [claim.get("group")]) if claim else set()
        if not claim or not (groups & allowed if generic else clean_text(claim.get("group")) in allowed):
            return [], f"Destino {instruction.get('claim_id') or 'sin identificar'}: no existe como partida vigente de una empresa autorizada. Revise o retire ese destino."
        claim_period = clean_text(claim.get("period"))
        if periods and claim_period not in periods and not (claim.get("kind") == "X" and not claim_period):
            if not allow_other_periods:
                return [], f"{_destination_label(claim)}: el período {claim_period or 'sin identificar'} está fuera de los períodos del detalle. Revise Períodos a conciliar o autorice otros períodos."
            if not claim_period or claim.get("period_closed"):
                return [], f"{_destination_label(claim)}: el período no está abierto. Reabra o corrija el período antes de vincular."
            other_periods.add(claim_period)
        if generic:
            company = clean_text(row.get("employer")) or (next(iter(groups & allowed)) if len(groups & allowed) == 1 else "")
            if not company or company not in groups & allowed:
                return [], "Indique una empresa autorizada en la fila para distribuir la partida genérica"
            if instruction.get("group") and instruction["group"] != company:
                return [], "La empresa del destino genérico no coincide con la fila del detalle"
            instruction["group"] = company
        elif _unidentified_complement_link(row, claim, instruction):
            unidentified_complements.append(claim["id"].removeprefix("X:"))
        elif not _candidate_matches(row, claim):
            if not _manual_application_identity_matches(row, claim, employer):
                return [], f"{_destination_label(claim)}: {_candidate_mismatch(row, claim)}"
            different_references.append(claim["id"].removeprefix("H:"))
        if not money(instruction['amount_usd']):
            return [], f"{_destination_label(claim)}: indique un importe distinto de cero para el vínculo."
        if money(instruction['amount_usd']) < 0 and claim.get('kind') != 'X':
            return [], f"{_destination_label(claim)}: el importe debe ser positivo; solo las partidas complementarias admiten importes negativos."
        targets.append({"claim_id": claim["id"], "amount_usd": money_float(instruction["amount_usd"]),
                        "instruction_id": instruction["id"],
                        **({"group": instruction["group"]} if generic else {})})
    if sum_money(target["amount_usd"] for target in targets) != money(amount_usd):
        assigned = sum_money(target['amount_usd'] for target in targets)
        return [], f"Los destinos vinculados suman US$ {assigned:.2f}; la fila requiere US$ {money(amount_usd):.2f}. Ajuste los importes vinculados (diferencia US$ {money(amount_usd) - assigned:.2f})."
    reason = "Conciliación manual: destinos vinculados y validados contra la fila del detalle"
    if different_references:
        reason += "; referencias distintas aceptadas con cliente, crédito y empresa coincidentes: " + ", ".join(
            dict.fromkeys(different_references)
        )
    if other_periods:
        reason += "; vínculo autorizado con otros períodos: " + ", ".join(sorted(other_periods))
    if unidentified_complements:
        reason += "; complementarias sin cliente/crédito atribuidas por vínculo manual explícito: " + ", ".join(
            dict.fromkeys(unidentified_complements)
        )
    return targets, reason


def suggest_detail_targets(
    row: Mapping[str, Any], claims: Iterable[Mapping[str, Any]],
    amount_usd: float, employer: str, period: str | Iterable[str] = "",
    tolerance_usd: float = 0,
    allowed_groups=None,
    apply_fifo=False, reserved_amounts=None,
) -> tuple[list[dict[str, Any]], str]:
    """Suggest only exact, uniquely attributable claims; never fuzzy-match names."""
    periods = _period_scope(period)
    if apply_fifo and not periods:
        return [], "Seleccione los períodos a conciliar antes de aplicar FIFO"
    claims = [
        claim for claim in claims
        if not claim.get("manual_only")
        and clean_text(claim.get("group")) in set(allowed_groups or [employer])
        and (not periods or clean_text(claim.get("period")) in periods)
        and money(claim.get("amount_usd")) > 0
        and (not apply_fifo or (claim.get("kind") in {"H", "C"} and not claim.get("period_closed")))
    ]
    name_only = not any(clean_text(row.get(field)) for field in (
        "loan_number", "client_number", "employee_number", "national_id",
    ))
    row_key = clean_text(row.get("row_key"))
    if row_key:
        keyed = [
            claim for claim in claims
            if claim.get("kind") == "C" and clean_text(claim.get("row_key")) == row_key
        ]
        if len(keyed) > 1:
            return [], "Fila ID duplicada"
        if keyed:
            # The collection claim may be net of an administrative fee. Keep
            # complementary claims so a single company row can cover both.
            claims = keyed + [claim for claim in claims if claim.get("kind") == "X"]
    matches = [claim for claim in claims if _candidate_matches(row, claim)]
    if not matches:
        return [], (
            "Sin coincidencia exacta por nombre/alias"
            if name_only else "Sin aplicación o cobranza identificable"
        )
    if len({clean_text(claim.get("group")) for claim in matches}) > 1:
        return [], "Nombre o identificador ambiguo entre empresas; seleccione la empresa de la fila"
    if apply_fifo:
        from credinomina_reconciliation.remittance_fifo import fifo_targets
        return fifo_targets(matches, amount_usd, reserved_amounts)
    if len(matches) == 1:
        claim = matches[0]
        identity_note = (
            "; core sin identidad del cliente"
            if not claim.get("client_number") and not claim.get("employee_number") and not claim.get("national_id")
            else ""
        )
        if row.get("installment_number") and not claim.get("installment_number"):
            identity_note += "; core sin número de cuota"
        if money(amount_usd) > money(claim["amount_usd"]) + decimal_value(EPSILON):
            if (
                claim.get("kind") not in {"C", "H"}
                or len(claim.get("application_ids") or ()) != 1
                or money(amount_usd) - money(claim["amount_usd"])
                > money(tolerance_usd) + decimal_value(EPSILON)
            ):
                return [], "Importe supera el destino; revise partidas complementarias"
            return [{
                "claim_id": claim["id"],
                "amount_usd": money_float(claim["amount_usd"]),
            }], "Coincidencia única con diferencia de tolerancia" + identity_note
        reason = "Coincidencia única por nombre/alias" if name_only else "Coincidencia única"
        return [{"claim_id": claim["id"], "amount_usd": money_float(amount_usd)}], reason + identity_note
    kinds = {claim.get("kind") for claim in matches}
    if "C" in kinds and "H" in kinds:
        return [], "Coincidencia entre histórico y operativo: seleccione período"
    if not any((row.get("loan_number"), row.get("client_number"), row.get("employee_number"), row.get("national_id"))):
        people = {
            canonical_identifier(claim.get("client_number"))
            or canonical_identifier(claim.get("national_id"))
            or claim.get("client")
            for claim in matches
        }
        if len(people) != 1 or "" in people:
            return [], "Nombre compartido o sin identidad única; indique destinos manuales"
    total = sum_money(claim["amount_usd"] for claim in matches)
    if abs(total - money(amount_usd)) > decimal_value(EPSILON):
        return [], "Varias aplicaciones posibles; indique destinos manuales"
    return [
        {"claim_id": claim["id"], "amount_usd": money_float(claim["amount_usd"])}
        for claim in matches
    ], (
        "Varias aplicaciones cubiertas íntegramente por nombre/alias"
        if name_only else "Varias aplicaciones cubiertas íntegramente"
    )
