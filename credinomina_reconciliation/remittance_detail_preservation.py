"""Preserve unambiguous child identities on import; never infer a new cash split."""
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.deposit_difference import is_difference
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.rounding import money

IDENTIFIERS = ("client_number", "employee_number", "national_id", "client_name")


def _amounts(row):
    return tuple(money(row.get(field)) for field in ("deducted_usd", "deducted_nio", "amount_usd"))


def _compatible(old, new):
    # Position in the spreadsheet is not identity: sorting must keep links.
    if old.get("amount_usd") is None or new.get("amount_usd") is None or _amounts(old) != _amounts(new):
        return False
    proof = False
    for field in IDENTIFIERS:
        left, right = canonical_identifier(old.get(field)), canonical_identifier(new.get(field))
        if left and right:
            if left != right:
                return False
            proof = True
    if not proof:
        return False
    for field in ("client", "employer", "loan_number", "installment_number", "row_key", "application_reference"):
        normalize = canonical_credit_number if field == "loan_number" else clean_text
        left, right = normalize(old.get(field)), normalize(new.get(field))
        if left and right and left != right:
            return False
    return True


def preserved_rows(document, candidates):
    """Plan the complete replacement before mutating rows or their targets."""
    old_rows = list(document.get("detail_rows") or [])
    old_by_name = {row.name: row for row in old_rows}
    protected = {target.detail_row for target in document.get("targets") or [] if target.get("detail_row")}
    differences = [row for row in old_rows if is_difference(row)]
    matches, reverse, candidates_by_identity = {}, defaultdict(list), defaultdict(set)
    for index, candidate in enumerate(candidates):
        for field in IDENTIFIERS:
            value = canonical_identifier(candidate.get(field))
            if value:
                candidates_by_identity[(field, value, _amounts(candidate))].add(index)
    for row in old_rows:
        if is_difference(row):
            continue
        possible = set()
        for field in IDENTIFIERS:
            value = canonical_identifier(row.get(field))
            if value:
                possible.update(candidates_by_identity.get((field, value, _amounts(row)), ()))
        matches[row.name] = [index for index in sorted(possible) if _compatible(row, candidates[index])]
        for index in matches[row.name]:
            reverse[index].append(row.name)
    unique = {name: positions[0] for name, positions in matches.items()
              if len(positions) == 1 and len(reverse[positions[0]]) == 1}
    for name in protected:
        old = old_by_name.get(name)
        if old is not None and (is_difference(old) or name in unique):
            continue
        label = (old.get("source_row") or old.get("idx") or name) if old else name
        frappe.throw(_("No se puede reemplazar el detalle: la fila vinculada {0} desapareció, cambió de importe o no tiene una coincidencia única en el nuevo archivo. "
            "Revise el archivo o corrija explícitamente sus vínculos antes de reimportar. No se modificaron los destinos ni las conciliaciones.").format(label))
    result = list(candidates)
    for name, index in unique.items():
        old = old_by_name[name]
        saved = old.as_dict() if callable(getattr(old, "as_dict", None)) else dict(old)
        # Keep manual identity choices, documented differences and link evidence.
        # Only source position/comments change for a proven identical deduction.
        result[index] = {**saved, **{field: candidates[index].get(field)
            for field in ("source_row", "comments", "application_comment")}}
    return result + differences


def validate_application_rows(document_name, rows, instructions, deposit_id):
    """Detail-backed deposits cannot execute orphan application instructions."""
    if not rows:
        return
    names = {row.name for row in rows if not is_difference(row)}
    invalid = [entry for entry in instructions if entry.get("deposit_id") == deposit_id
        and str(entry.get("claim_id") or "").startswith(("H:", "C:"))
        and entry.get("detail_row") not in names]
    if invalid:
        frappe.throw(_("No se pudo conciliar el depósito {0}: tiene detalle y hay destinos de aplicaciones sin una fila del detalle válida. "
            "Use Vincular detalle y destinos para asociarlos antes de conciliar. Destinos: {1}. "
            "Se conservaron las conciliaciones existentes; no se guardó ningún cambio.").format(
                document_name, ", ".join(str(entry.get("claim_id")) for entry in invalid)))
