"""Release one detail row's cash without replaying any other deposit decisions."""
import json

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.remittance_selection import target_key


def _claim_key(claim_id):
    kind, _, name = str(claim_id or "").partition(":")
    if kind in {"H", "X"} and name:
        return kind, name
    if kind == "C" and name:
        row = frappe.db.get_value("CN Collection Row", name, ["parent", "row_key"], as_dict=True)
        if row:
            return "C", row.parent, row.row_key
    return None


def split_row_allocations(document, row_name):
    """Require exact row provenance, or a destination owned by exactly one row."""
    entries = json.loads(document.get("allocation_detail") or "[]")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        frappe.throw(_("La distribución guardada no es válida. Revise el depósito."))
    if sum_money(entry.get("importe_usd") for entry in entries) != money(document.get("allocated_usd")):
        frappe.throw(_("La distribución guardada no coincide con el importe aplicado. Revise el depósito."))
    owners = {}
    for row in document.get("detail_rows") or []:
        for target in json.loads(row.get("matched_targets") or "[]"):
            key = _claim_key(target.get("claim_id"))
            if key:
                owners.setdefault(key, set()).add(row.name)
    for target in document.get("targets") or []:
        key = target_key(target)
        if key:
            owners.setdefault(key, set()).add(target.get("detail_row") or "__unassigned__")
    removed, retained = [], []
    for entry in entries:
        key = target_key(entry)
        explicit = entry.get("fila_detalle")
        matches = owners.get(key, set())
        if not explicit and row_name in matches and matches != {row_name}:
            frappe.throw(_("Un destino está compartido por varias filas o no tiene fila asignada. Revise sus vínculos antes de desconciliar esta fila."))
        selected = explicit == row_name or (not explicit and matches == {row_name})
        if selected and (not key or key[0] not in {"H", "C", "X"}
                         or key[0] != "X" and money(entry.get("importe_usd")) < 0):
            frappe.throw(_("La fila contiene una distribución no identificable. Revise sus vínculos antes de desconciliarla."))
        (removed if selected else retained).append(entry)
    return removed, retained


@frappe.whitelist(methods=["POST"])
def unreconcile_row(remittance_name, detail_row_name, modified):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    from credinomina_reconciliation.complementary_cancellation import _deposit_periods
    from credinomina_reconciliation.manual_complementary_policy import assert_no_legacy_tolerance
    from credinomina_reconciliation.reconciliation_audit import snapshot, record_transition
    from credinomina_reconciliation.deposit_presentation import coverage_totals
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine

    doc = frappe.get_doc("CN Remittance Allocation", remittance_name)
    doc.check_permission("write")
    if not frappe.has_permission("CN Accounting Import", "write"):
        frappe.throw(_("No tiene permiso para actualizar la conciliación de aplicaciones."), frappe.PermissionError)
    lock_cash_pool(reconciliation_companies(doc.employer))
    frappe.db.get_value(doc.doctype, doc.name, "name", for_update=True)
    doc.reload()
    doc.check_permission("write")
    if doc.docstatus != 1 or str(doc.modified) != str(modified):
        frappe.throw(_("El depósito cambió o no está confirmado. Recargue antes de desconciliar."))
    if doc.get("consolidated_deposit") or doc.get("cash_identity_status") == "Origen vinculado":
        frappe.throw(_("Abra el depósito principal para modificar su conciliación."))
    row = next((row for row in doc.detail_rows if row.name == detail_row_name), None)
    if not row or row.get("row_kind") == "Diferencia documentada":
        frappe.throw(_("Seleccione una fila de cliente del depósito."))
    assert_no_legacy_tolerance({"deposit_source_row": doc.name})
    periods = sorted(_deposit_periods([doc]))
    closed = frappe.get_all("CN Reconciliation Period", filters={"name": ["in", periods], "status": "Cerrado"}, pluck="name") if periods else []
    if closed:
        frappe.throw(_("Reabra los períodos {0} antes de desconciliar esta fila.").format(", ".join(closed)))
    removed, retained = split_row_allocations(doc, row.name)
    removed_targets = [target.as_dict() for target in doc.targets if target.get("detail_row") == row.name]
    complements = {entry.get("partida") for entry in removed} | {target.get("complementary_item") for target in removed_targets}
    for name in sorted(complements - {None, ""}):
        frappe.get_doc("CN Complementary Item", name).check_permission("write")
    released = sum_money(entry.get("importe_usd") for entry in removed)
    if released < 0 or released > money(doc.allocated_usd):
        frappe.throw(_("Los movimientos de la fila no pueden retirarse sin alterar el efectivo de otras filas. Revise sus vínculos."))
    if (not removed and money(row.get("linked_usd")) and
            (str(row.get("match_status") or "").startswith("Conciliada") or
             row.get("match_status") == "Cobertura parcial; diferencia de empresa")):
        frappe.throw(_("No se pudo identificar la distribución de esta fila. Revise sus vínculos antes de desconciliarla."))
    if money(doc.allocated_usd) + money(doc.unallocated_usd) != money(doc.amount_usd):
        frappe.throw(_("Los saldos guardados del depósito no coinciden con el efectivo. Revise el depósito."))
    before = snapshot(doc)
    other_rows = {detail.name: {field: detail.get(field) for field in (
        "matched_targets", "linked_usd", "pending_usd", "match_status", "deducted_usd", "deducted_nio")}
        for detail in doc.detail_rows if detail.name != row.name}
    doc.set("targets", [target for target in doc.targets if target.get("detail_row") != row.name])
    row.manual_reconciliation_required = 1
    doc.save()
    values = dict(allocation_detail=json.dumps(retained, ensure_ascii=False),
        allocated_usd=money_float(money(doc.allocated_usd) - released),
        unallocated_usd=money_float(money(doc.unallocated_usd) + released),
        unclassified_usd=money_float(money(doc.unclassified_usd) + released),
        detail_status="Revisar filas", result="Revisar detalle", financial_status="Revisar detalle")
    values.update(coverage_totals({**doc.as_dict(), **values}))
    frappe.db.set_value(doc.doctype, doc.name, values)
    frappe.db.set_value(row.doctype, row.name, dict(
        manual_reconciliation_required=1, match_status="Pendiente manual",
        match_reason=_("Fila desconciliada. Seleccione los destinos manualmente; la conciliación automática está desactivada para esta fila."),
        matched_targets="[]", matched_targets_summary="", linked_usd=0,
        pending_usd=money_float(money(row.amount_usd) - money(row.client_credit_usd) - money(row.company_credit_usd))), update_modified=False)
    # Rebuild application/period balances from stored cash only. No matcher is
    # allowed to spend the released amount on this or any other detail row.
    for period_name in periods:
        employer = frappe.db.get_value("CN Reconciliation Period", period_name, "employer")
        engine._reconcile_sources(employer, preserve_deposits=True, period_name=period_name)
    doc.reload()
    for detail in doc.detail_rows:
        if detail.name in other_rows and any(detail.get(field) != value for field, value in other_rows[detail.name].items()):
            frappe.throw(_("Otra fila cambió durante la operación. No se guardó la desconciliación; recargue el depósito."))
    from credinomina_reconciliation.complementary_state import deposit_changed
    deposit_changed(doc, previous=before)
    record_transition(doc.name, before, snapshot(doc))
    doc.add_comment("Comment", _("Fila {0} desconciliada; selección manual requerida. Efectivo liberado: USD {1}. Evidencia anterior: {2}").format(
        row.idx, released, frappe.utils.escape_html(frappe.as_json({"allocations": removed, "targets": removed_targets}))))
    return {"deposit": doc.name, "row": row.name, "released_usd": money_float(released), "periods": periods}
