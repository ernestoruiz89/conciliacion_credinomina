"""Audited return to draft; restoring a record never replays its cash links."""
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import escape_html, now_datetime

ITEM = "CN Complementary Item"
DEPOSIT = "CN Remittance Allocation"
IMPORT = "CN Accounting Import"
SUPPORTED = {ITEM, DEPOSIT, IMPORT}


@contextmanager
def _atomic():
    point = "cn_reopen_document"
    frappe.db.savepoint(point)
    try:
        yield
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _load(doctype, name, modified, status, reason):
    if doctype not in SUPPORTED:
        frappe.throw(_("Este documento no admite volver a borrador."))
    if not str(reason or "").strip():
        frappe.throw(_("Indique el motivo para volver a borrador."))
    document = frappe.get_doc(doctype, name, for_update=True)
    document.check_permission("write")
    if document.docstatus != status or str(document.modified) != str(modified):
        frappe.throw(_("El documento cambió. Recargue el formulario antes de continuar."))
    if frappe.db.exists("Workflow", {"document_type": doctype, "is_active": 1}):
        frappe.throw(_("Este documento tiene un flujo de trabajo activo. Utilice sus transiciones."))
    return document


def _item_can_reopen(item):
    if item.get("origin_deposit") or item.get("receivable_origin") or item.category in {
            "Diferencia por tolerancia", "Compensación entre partidas", "Vinculado a depósito", "Cobro de CxC"}:
        frappe.throw(_("Esta partida tiene un tratamiento que no permite volver a borrador. Revise su documento de origen."))


def _draft(document, values=None, children=None):
    """Only this parent and its existing children change status; no save hooks."""
    before = frappe.get_doc(document.as_dict())
    values = dict(values or {}, docstatus=0, modified=now_datetime(), modified_by=frappe.session.user)
    frappe.db.set_value(document.doctype, document.name, values, update_modified=False)
    document.update(values)
    for field in document.meta.get_table_fields():
        for row in document.get(field.fieldname) or []:
            changes = dict((children or {}).get(row.doctype, {}), docstatus=0)
            frappe.db.set_value(row.doctype, row.name, changes, update_modified=False)
            row.update(changes)
    return before


def _audit(document, before, action, reason):
    document.reload()
    document._doc_before_save = before
    document.save_version()
    document.add_comment("Edit", _("{0} por {1}. Motivo: {2}. El documento vuelve a borrador con el mismo número.").format(
        action, escape_html(frappe.session.user), escape_html(str(reason).strip())))
    document.run_method("on_change")
    document.notify_update()
    frappe.clear_document_cache(document.doctype, document.name)


def unconfirm_complement(name, modified, reason):
    with _atomic():
        item = _load(ITEM, name, modified, 1, reason)
        item.check_permission("cancel")
        _item_can_reopen(item)
        from credinomina_reconciliation.manual_origin_link import guard_removal
        guard_removal(item)
        item._prepare_deactivation()
        values = {"review_status": "Ajuste pendiente de confirmar"} if item.category == "Ajuste de aplicación" else {}
        before = _draft(item, values)
        from credinomina_reconciliation.complementary_cancellation import reconcile_scoped
        scope = reconcile_scoped(item, item.flags.cancellation_scope, action="desconfirmar la partida")
        from credinomina_reconciliation.reconciliation_trace import capture_item_confirmation
        capture_item_confirmation(item, "on_unconfirm")
        _audit(item, before, "Desconfirmación de partida", reason)
        return dict(name=item.name, docstatus=0, deposits=scope.get("deposits", []), periods=scope.get("periods", []))


@frappe.whitelist(methods=["POST"])
def undo_cancellation(doctype: str, name: str, modified: str, reason: str):
    """Restore an inactive draft using document permissions, preserving core evidence."""
    with _atomic():
        document = _load(doctype, name, modified, 2, reason)
        document.check_permission("delete")
        if document.meta.is_submittable:
            document.check_permission("cancel")
        from credinomina_reconciliation.manual_origin_link import guard_removal
        guard_removal(document)
        values, children = {}, {}
        if doctype == ITEM:
            _item_can_reopen(document)
            # Historical adjustment links are retained as planned destinations;
            # only submitted items may reduce an application's balance.
            if document.category == "Ajuste de aplicación":
                values["review_status"] = "Ajuste pendiente de confirmar"
        elif doctype == DEPOSIT:
            values = dict(allocated_usd=0, unallocated_usd=document.amount_usd,
                unclassified_usd=document.amount_usd, justified_surplus_usd=0,
                credit_coverage_usd=0, complementary_coverage_usd=0, conversion_coverage_usd=0,
                allocation_detail="[]", result="Pendiente", financial_status="Pendiente",
                detail_status="Detalle pendiente", inherited_exception_comment="")
            children["CN Remittance Target"] = dict(result="Pendiente")
        else:
            # Imports normally remain drafts. A legacy canceled import must not
            # become an active source just because its cancellation was undone.
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _guard_source_removal
            _guard_source_removal(document)
            values["status"] = "Borrador"
            children["CN Source Row"] = dict(effective=0, match_status="Pendiente")
        before = _draft(document, values, children)
        if doctype == DEPOSIT:
            for row in document.detail_rows:
                changes = dict(linked_usd=0, pending_usd=row.amount_usd, client_credit_usd=0,
                    company_credit_usd=0, matched_targets="[]", matched_targets_summary="",
                    match_status="Pendiente", match_reason="Cancelación deshecha; requiere confirmación y conciliación.")
                frappe.db.set_value(row.doctype, row.name, changes, update_modified=False)
        _audit(document, before, "Cancelación deshecha", reason)
        return dict(name=document.name, docstatus=0)
