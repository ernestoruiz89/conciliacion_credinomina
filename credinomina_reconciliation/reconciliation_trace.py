"""Append-only attribution of individual reconciliation allocations.

The initial migration is explicitly an attribution to the last document editor,
not a reconstruction of past allocations. Financial writes and their traces use
the same transaction; reading a report never writes or recalculates balances.
"""
from collections import defaultdict
from hashlib import sha256
import json

import frappe
from frappe.utils import now_datetime

from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.report_records import _pages

DOCTYPE = "CN Reconciliation Trace"
APPLICATION = "CN Source Row"
COLLECTION = "CN Collection Row"
DEPOSIT = "CN Remittance Allocation"
ITEM = "CN Complementary Item"
INITIAL = "Último editor (migración)"


def entries(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def append(source_type, source, target_type, target, *, key, user, timestamp,
           before=0, after=0, description="", detail_row="", origin="Operación", evidence=""):
    if not source or not target or not user or not timestamp:
        return
    if origin != INITIAL and money(before) == money(after):
        return
    name = sha256(json.dumps([source_type, source, target_type, target, key],
                            ensure_ascii=False).encode()).hexdigest()
    # The deterministic primary key makes journal rescans and the patch idempotent.
    if frappe.db.exists(DOCTYPE, name):
        return
    doc = frappe.get_doc(dict(doctype=DOCTYPE, name=name, source_doctype=source_type,
        source_name=source, target_doctype=target_type, target_name=target,
        performed_by=user, performed_on=timestamp, origin=origin, evidence=evidence,
        before_usd=money_float(before), after_usd=money_float(after),
        delta_usd=money_float(money(after) - money(before)), detail_row=detail_row,
        description=description))
    doc.insert(ignore_permissions=True, ignore_links=True, ignore_if_duplicate=True, set_name=name)


def capture_saved_item(name):
    capture_item_journals(frappe.get_doc(ITEM, name))


def _allocation_index(raw):
    result = defaultdict(lambda: money(0))
    for row in entries(raw):
        kind = row.get("tipo")
        parts = entries(row.get("aplicaciones_fifo"))
        if kind == "Cobranza" and parts and all(part.get("application_id") for part in parts) and sum(
                (money(part.get("amount_usd")) for part in parts), money(0)) == money(row.get("importe_usd")):
            for part in parts:
                result[(APPLICATION, part["application_id"], row.get("fila_detalle") or "")] += money(part.get("amount_usd"))
            continue
        if kind == "Aplicacion historica":
            target_type, target = APPLICATION, row.get("aplicacion_id")
        elif kind == "Partida complementaria":
            target_type, target = ITEM, row.get("partida")
        elif kind == "Cobranza":
            target_type = COLLECTION
            target = frappe.db.get_value(COLLECTION, {"parent": row.get("periodo"),
                "parenttype": "CN Reconciliation Period", "parentfield": "collection_rows",
                "row_key": row.get("fila_id")}, "name") if row.get("periodo") and row.get("fila_id") else None
        else:
            continue
        if target:
            result[(target_type, target, row.get("fila_detalle") or "")] += money(row.get("importe_usd"))
    return result


def capture_version(version, method=None):
    """Only the authoritative cash audit, not the duplicate generic save Version."""
    if frappe.flags.in_migrate or version.get("ref_doctype") != DEPOSIT:
        return
    data = frappe.parse_json(version.get("data") or "{}")
    audit = data.get("cn_reconciliation") if isinstance(data, dict) else None
    if not audit:
        return
    before, after = audit.get("before") or {}, audit.get("after") or {}
    old, new = _allocation_index(before.get("allocation_detail")), _allocation_index(after.get("allocation_detail"))
    for target_type, target, detail in sorted(old.keys() | new.keys()):
        identity = (target_type, target, detail)
        append(DEPOSIT, version.docname, target_type, target,
            key=[version.name, detail], user=version.owner, timestamp=version.creation,
            before=old[identity], after=new[identity], detail_row=detail,
            description=audit.get("action") or "Distribución de depósito", evidence=version.name)
    # Documented surplus is an operation on the deposit itself, not on a loan.
    append(DEPOSIT, version.docname, DEPOSIT, version.docname,
        key=[version.name, "surplus"], user=version.owner, timestamp=version.creation,
        before=before.get("justified_surplus_usd"), after=after.get("justified_surplus_usd"),
        description="Saldo a favor documentado", evidence=version.name)


def capture_deposit_cancel(doc, method=None):
    if frappe.flags.in_migrate:
        return
    previous = doc.get_doc_before_save()
    if previous:
        from credinomina_reconciliation.reconciliation_audit import snapshot, record_transition, audit_reason
        before = snapshot(previous)
        after = dict(before, allocated_usd=0, justified_surplus_usd=0, allocation_detail="[]")
        with audit_reason("Cancelar depósito"):
            record_transition(doc.name, before, after)


def capture_item_journals(doc, method=None):
    """Server-authored journals already identify the actor of each portion."""
    if frappe.flags.in_migrate or doc.get("doctype") != ITEM:
        return
    for row in doc.get("compensations") or []:
        append(ITEM, doc.name, ITEM, doc.name, key=["offset", row.get("entry_key") or row.get("name")],
            user=row.get("confirmed_by"), timestamp=row.get("confirmed_on"), after=row.get("amount_usd"),
            description="Compensación con " + str(row.get("counterpart") or ""))
    if doc.get("docstatus") != 1:
        return
    for row in entries(doc.get("credit_history")):
        if row.get("application_id"):
            append(ITEM, doc.name, APPLICATION, row["application_id"], key=["credit", row.get("operation_id")],
                user=row.get("usuario"), timestamp=row.get("registrado_el"), after=row.get("importe_usd"),
                description=row.get("tratamiento") or "Aplicación de saldo a favor")
    for row in entries(doc.get("reclassification_allocations")):
        _capture_reclassification(doc, row, row, "reclassification")
    for deletion in entries(doc.get("reclassification_deletions")):
        if isinstance(deletion.get("removed"), dict):
            _capture_reclassification(doc, deletion["removed"], deletion, "removed")


def _capture_reclassification(doc, row, actor, prefix):
    application = row.get("kind") == "Pago pendiente contra aplicación"
    append(ITEM, doc.name, APPLICATION if application else DEPOSIT,
        row.get("target") if application else row.get("parent_document"),
        key=[prefix, actor.get("operation_id")], user=actor.get("recorded_by") or actor.get("user"),
        timestamp=actor.get("recorded_on"), after=money(row.get("amount_usd")) * (-1 if prefix == "removed" else 1),
        detail_row="" if application else row.get("target"), description=row.get("kind") or "Reclasificación")


def capture_adjustment_additions(doc, additions):
    if frappe.flags.in_migrate or doc.docstatus != 1:
        return
    for row in additions:
        append(ITEM, doc.name, APPLICATION, row.get("related_application"),
            key=["adjustment", row.get("related_application"), row.get("recorded_on")],
            user=row.get("recorded_by"), timestamp=row.get("recorded_on"),
            after=row.get("application_adjustment_usd"), description="Ajuste adicional de aplicación")


def capture_item_confirmation(doc, method=None):
    """Confirmation/reversal of the original adjustment; additions have their own actor."""
    if frappe.flags.in_migrate or method not in {"on_submit", "on_cancel"}:
        return
    sign = -1 if method == "on_cancel" else 1
    if doc.get("category") == "Ajuste de aplicación":
        portions = [(doc.get("related_application"), doc.get("application_adjustment_usd"))]
        portions += [(row.get("related_application"), row.get("application_adjustment_usd"))
                     for row in entries(doc.get("application_adjustment_history"))]
        for index, (target, amount) in enumerate(portions):
            append(ITEM, doc.name, APPLICATION, target, key=[method, str(doc.modified), index],
                user=frappe.session.user, timestamp=now_datetime(), after=money(amount) * sign,
                description="Reversión de ajuste" if sign < 0 else "Confirmación de ajuste")
    elif not doc.get("compensations") and doc.get("category") in {"Saldo a favor del cliente", "Saldo a favor de la empresa"}:
        append(ITEM, doc.name, ITEM, doc.name, key=[method, str(doc.modified)],
            user=frappe.session.user, timestamp=now_datetime(), after=money(doc.get("amount_usd")) * sign,
            description="Reversión de saldo documentado" if sign < 0 else "Confirmación de saldo a favor")


def histories(kind, sources, collections=None, user=None):
    """Read only traces for authorized report rows and readable source documents."""
    targets = defaultdict(set)
    for row in sources:
        target_type = APPLICATION if kind == "Aplicaciones" else DEPOSIT if kind == "Depósitos" else ITEM
        targets[(target_type, row["name"])].add(row["name"])
        if kind == "Aplicaciones":
            links = entries(row.get("application_allocation_detail"))
            if not links and row.get("collection_row_id"):
                links = [{"collection_row_id": row["collection_row_id"]}]
            for link in links:
                claim = link.get("collection_row_id")
                if claim in (collections or {}):
                    targets[(COLLECTION, claim)].add(row["name"])
    output = defaultdict(list)
    traces = {}
    for target_type in {key[0] for key in targets}:
        names = sorted(key[1] for key in targets if key[0] == target_type)
        for offset in range(0, len(names), 500):
            for side in ("target", "source") if target_type in {DEPOSIT, ITEM} else ("target",):
                filters = {side + "_doctype": target_type, side + "_name": ["in", names[offset:offset + 500]]}
                if user:
                    filters["performed_by"] = user
                for trace in _pages(frappe.get_all, DOCTYPE, filters=filters,
                        fields=["name", "source_doctype", "source_name", "target_doctype", "target_name", "performed_by",
                                "performed_on", "origin", "before_usd", "after_usd", "delta_usd", "description", "detail_row"],
                        order_by="performed_on asc, name asc"):
                    traces[trace.name] = trace
    # A target's visibility is not permission to reveal a restricted cash origin.
    visible = set()
    for source_type in {row.source_doctype for row in traces.values()}:
        if not frappe.has_permission(source_type, "read"):
            continue
        names = sorted({row.source_name for row in traces.values() if row.source_doctype == source_type})
        for offset in range(0, len(names), 500):
            visible.update((source_type, name) for name in frappe.get_list(source_type,
                filters={"name": ["in", names[offset:offset + 500]]}, pluck="name", limit_page_length=0))
    for trace in sorted(traces.values(), key=lambda row: (str(row.performed_on), row.name)):
        source = (trace.source_doctype, trace.source_name)
        if source not in visible:
            continue
        names = targets.get((trace.target_doctype, trace.target_name), set()) | targets.get(source, set())
        # Only return origin and amounts, never another target's identifier.
        detail = {field: trace.get(field) for field in ("performed_by", "performed_on", "origin", "before_usd",
            "after_usd", "delta_usd", "description", "source_doctype", "source_name", "detail_row")}
        detail["shared_collection"] = trace.target_doctype == COLLECTION
        for name in names:
            output[name].append(detail)
    return output
