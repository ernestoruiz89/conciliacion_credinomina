"""Recover row provenance lost by an unchanged-file reimport, never cash links."""
import copy
import json
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.allocation_origin import FIFO
from credinomina_reconciliation.deposit_reconciliation import entries, stored_entries
from credinomina_reconciliation.parsers import clean_text, canonical_credit_number
from credinomina_reconciliation.rounding import money, money_float


def _signature(row):
    # Credit and employer can have been enriched after import. The original
    # source position, person and deductions must still be identical.
    return tuple(clean_text(row.get(field)) for field in (
        "source_row", "row_key", "employee_number", "national_id",
        "application_reference", "installment_number")) + (
        money(row.get("deducted_usd")), money(row.get("deducted_nio")))


def _compatible_identity(old, new):
    return (any(old.get(field) and old.get(field) == new.get(field)
                for field in ("client_number", "national_id", "employee_number"))
        and not any(new.get(field) and new.get(field) != old.get(field)
                    for field in ("client", "employer", "client_number"))
        and (not new.get("loan_number") or canonical_credit_number(new.get("loan_number")) == canonical_credit_number(old.get("loan_number"))))


def recovery_plan(document, versions):
    current = {row.name: row for row in document.get("detail_rows") or []}
    ledger = copy.deepcopy(stored_entries(document))
    orphaned = defaultdict(list)
    for entry in ledger:
        if entry.get("fila_detalle") and entry["fila_detalle"] not in current:
            orphaned[entry["fila_detalle"]].append(entry)
    if not orphaned:
        return None
    edges, snapshots = {}, {}
    for version in versions:
        try:
            data = json.loads(version["data"])
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        # Only a row replacement with unchanged file and financial basis can
        # prove continuity. An edited file never supplies a recovery edge.
        if any(change[0] in {"detail_hash", "detail_file", "detail_source_file", "fx_rate",
                            "employer", "deposit_amount", "amount_usd"} for change in data.get("changed", [])):
            continue
        added, removed = defaultdict(list), defaultdict(list)
        for field, row in data.get("added", []):
            if field == "detail_rows" and row.get("row_kind") != "Diferencia documentada":
                added[_signature(row)].append(row)
        for field, row in data.get("removed", []):
            if field == "detail_rows" and row.get("row_kind") != "Diferencia documentada":
                removed[_signature(row)].append(row)
        for signature, old in removed.items():
            new = added.get(signature, [])
            if (len(old) == len(new) == 1 and old[0].get("name") and new[0].get("name")
                    and _compatible_identity(old[0], new[0])):
                edges[old[0]["name"]] = new[0]["name"]
                snapshots[old[0]["name"]] = old[0]
    mapping, updates = {}, {}
    for old_name, portions in orphaned.items():
        name, visited = old_name, set()
        while name in edges and name not in visited:
            visited.add(name)
            name = edges[name]
        old, row = snapshots.get(old_name), current.get(name)
        if (not old or not row or _signature(old) != _signature(row)
                or not _compatible_identity(old, row)
                or name in mapping.values() or not old.get("client") or not old.get("employer") or not old.get("loan_number")):
            return None
        saved = entries(old.get("matched_targets"))
        expected, matched = {}, {}
        for part in portions:
            if (part.get("tipo") != "Aplicacion historica" or part.get("origen") != FIFO
                    or part.get("reclassification_operation_id") or money(part.get("importe_usd")) <= 0):
                return None
            claim = "H:" + str(part.get("aplicacion_id") or "")
            if claim in expected:
                return None
            expected[claim] = money(part["importe_usd"])
            pieces = part.get("aplicaciones_fifo")
            if (not isinstance(pieces, list) or not pieces
                    or any(not isinstance(piece, dict) or piece.get("application_id") != part.get("aplicacion_id")
                           or money(piece.get("amount_usd")) <= 0 for piece in pieces)
                    or sum((money(piece["amount_usd"]) for piece in pieces), money(0)) != expected[claim]):
                return None
        for part in saved:
            claim = part.get("claim_id")
            if claim in matched:
                return None
            matched[claim] = money(part.get("amount_usd"))
        linked = sum(expected.values(), money(0))
        if matched != expected or linked > money(row.get("amount_usd")):
            return None
        mapping[old_name] = name
        updates[name] = {field: old.get(field) for field in ("client", "client_number", "employer", "loan_number", "identity_reason")}
        updates[name].update(matched_targets=json.dumps(saved, ensure_ascii=False),
            linked_usd=money_float(linked), pending_usd=money_float(money(row.get("amount_usd")) - linked))
    for entry in ledger:
        if entry.get("fila_detalle") in mapping:
            entry["fila_detalle"] = mapping[entry["fila_detalle"]]
    return dict(mapping=mapping, updates=updates, ledger=json.dumps(ledger, ensure_ascii=False))


def recover_reimported_detail(document):
    current = {row.name for row in document.get("detail_rows") or []}
    if not any(entry.get("fila_detalle") and entry["fila_detalle"] not in current for entry in stored_entries(document)):
        return
    versions = frappe.get_all("Version", filters={"ref_doctype": document.doctype, "docname": document.name},
        fields=["data"], order_by="creation asc, name asc", limit_page_length=2000)
    plan = recovery_plan(document, versions)
    if not plan:
        frappe.throw(_("El detalle del depósito fue reimportado y sus filas ya no coinciden con los vínculos confirmados. "
            "No se pudo recuperar su trazabilidad de forma inequívoca desde el historial. Revise el detalle y su historial; "
            "no se guardó ningún cambio."))
    from credinomina_reconciliation.client_credit import load_credits
    if load_credits([document.name]):
        frappe.throw(_("El detalle reimportado tiene saldos a favor confirmados. Revise su trazabilidad antes de conciliar; no se guardó ningún cambio."))
    # These writes share the reconciliation transaction. Any later validation
    # failure rolls them back along with the proposed distribution.
    for name, values in plan["updates"].items():
        frappe.db.set_value("CN Remittance Detail", name, values, update_modified=False)
    frappe.db.set_value(document.doctype, document.name, "allocation_detail", plan["ledger"], update_modified=False)
    from credinomina_reconciliation.reconciliation_audit import audit_reason, record_transition, snapshot
    original = snapshot(document)
    with audit_reason("Recuperar trazabilidad de detalle reimportado", "Mismo archivo; filas verificadas contra Version y vínculos FIFO guardados"):
        record_transition(document.name, original, {**original, "allocation_detail": plan["ledger"]})
    document.reload()
    return plan
