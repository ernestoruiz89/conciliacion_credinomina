"""Validation for matching a core reclassification to a deposit detail."""
from html import escape

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.rounding import money


def reconciliation_date(item, deposit):
    """The reconciliation cannot precede either original event."""
    origin = item.get("source_date") or item.get("posting_date")
    if not origin or not deposit.get("deposit_date"):
        frappe.throw(_("El depósito y la partida deben tener fecha para determinar la conciliación."))
    return max(getdate(origin), getdate(deposit.get("deposit_date")))


def credit_origin_date(deposit):
    if not deposit.get("deposit_date"):
        frappe.throw(_("El depósito debe tener fecha para determinar el saldo a favor."))
    return getdate(deposit.get("deposit_date"))


def validate_pair(item, deposit, row):
    """Reject uncertain or contradictory evidence before any financial write."""
    from credinomina_reconciliation.reclassification_distribution import allocations
    if (item.get("docstatus") != 1 or item.get("category") != "Compensación entre partidas"
            or not item.get("accounting_source_key") or money(item.get("source_debit")) <= 0
            or money(item.get("source_credit")) != 0
            or item.get("accounting_classification") in {"Aplicación de pago", "ND de Aplicación de pago"}):
        frappe.throw(_("Seleccione una partida confirmada del core con débito y sin crédito."))
    if (item.get("origin_deposit") or item.get("registered_deposit") or item.get("related_application")
            or item.get("registration_exception") or item.get("receivable_origin")
            or item.get("compensations") or item.get("generic_distribution") or allocations(item)):
        frappe.throw(_("La partida ya tiene un vínculo o tratamiento incompatible."))
    if deposit.get("docstatus") != 1 or row.get("parent") != deposit.get("name"):
        frappe.throw(_("Seleccione una fila de un depósito confirmado."))
    if not row.get("client") or not item.get("employer") or item.get("employer") != row.get("employer"):
        frappe.throw(_("La empresa y el cliente identificado deben coincidir con la fila del depósito."))
    if str(row.get("identity_reason") or "").startswith(("Conflicto", "Nombre ambiguo")):
        frappe.throw(_("La identidad de la fila tiene un conflicto pendiente de revisión."))
    item_loan, row_loan = canonical_credit_number(item.get("loan_number")), canonical_credit_number(row.get("loan_number"))
    if not row_loan or (item_loan and item_loan != row_loan):
        frappe.throw(_("El número de crédito de la partida debe coincidir con la fila del depósito."))
    item_number = canonical_identifier(item.get("client_number"))
    row_number = canonical_identifier(row.get("client_number"))
    if item_number and item_number != row_number:
        frappe.throw(_("El número de cliente de la partida no coincide con la fila."))
    if not name_key(item.get("client_name")) or name_key(item.get("client_name")) != name_key(row.get("client_name")):
        frappe.throw(_("El nombre del cliente de la partida no coincide con la fila."))
    if money(item.get("amount_usd")) <= 0 or money(row.get("amount_usd")) <= 0:
        frappe.throw(_("La partida y la fila del depósito deben tener importes positivos."))


def matching_rows(item, deposit):
    """Return candidates for explicit selection, never infer a missing loan."""
    rows = []
    plausible = 0
    item_number = canonical_identifier(item.get("client_number"))
    muted = frappe.flags.mute_messages
    try:
        frappe.flags.mute_messages = True
        for row in deposit.get("detail_rows") or []:
            row_number = canonical_identifier(row.get("client_number"))
            if (row.get("employer") == item.get("employer") and name_key(item.get("client_name"))
                    and name_key(item.get("client_name")) == name_key(row.get("client_name"))
                    and money(item.get("amount_usd")) > 0 and money(row.get("amount_usd")) > 0
                    and not (item_number and row_number and item_number != row_number)):
                plausible += 1
            try:
                validate_pair(item, deposit, row)
            except frappe.ValidationError:
                continue
            rows.append(row)
    finally:
        frappe.flags.mute_messages = muted
    ambiguous = not canonical_credit_number(item.get("loan_number")) and plausible > 1
    return rows, ambiguous


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def deposit_query(doctype, txt, searchfield, start, page_len, filters):
    from credinomina_reconciliation.paying_employers import load_payer_map
    item = frappe.get_doc("CN Complementary Item", (filters or {}).get("item_name"))
    item.check_permission("read")
    if not item.employer:
        return []
    companies = {item.employer} | {payer for payer, allowed in load_payer_map().items() if item.employer in allowed}
    rows = frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1, "employer": ["in", sorted(companies)],
        "consolidated_deposit": ["is", "not set"]},
        or_filters=[[field, "like", "%" + txt + "%"] for field in ("name", "deposit_reference", "deposit_voucher")],
        fields=["name", "deposit_date", "deposit_reference", "employer"], order_by="deposit_date desc, name desc",
        limit_start=int(start), limit_page_length=min(int(page_len), 50))
    return [(row.name, str(row.deposit_date), row.deposit_reference, row.employer) for row in rows]


@frappe.whitelist()
def preview(item_name, deposit_name):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
    deposit.check_permission("read")
    matches, ambiguous = matching_rows(item, deposit)
    rows = [{"name": row.name, "source_row": row.source_row, "client_name": row.client_name,
             "loan_number": row.loan_number, "amount_usd": row.amount_usd, "pending_usd": row.pending_usd}
            for row in matches]
    return {"item_modified": str(item.modified), "deposit_modified": str(deposit.modified),
            "credit_origin_date": str(credit_origin_date(deposit)),
            "reconciliation_date": str(reconciliation_date(item, deposit)), "rows": rows,
            "missing_loan": not bool(canonical_credit_number(item.get("loan_number"))), "ambiguous": ambiguous}


@frappe.whitelist(methods=["POST"])
def link(item_name, deposit_name, detail_row, item_modified, deposit_modified, commitment_date, reason):
    """Recognize one imported debit as a customer credit without another item."""
    from credinomina_reconciliation import client_credit
    from credinomina_reconciliation.complementary_state import mark
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.imported_complement_amount import ledger_magnitude

    deposit = client_credit.lock_credit_deposit(deposit_name)
    deposit.check_permission("write")
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if str(item.modified) != str(item_modified) or str(deposit.modified) != str(deposit_modified):
        frappe.throw(_("El depósito o la partida cambió. Recargue y vuelva a seleccionar la fila."))
    row = next((entry for entry in deposit.get("detail_rows") or [] if entry.name == detail_row), None)
    if not row:
        frappe.throw(_("La fila seleccionada ya no pertenece al depósito."))
    validate_pair(item, deposit, row)
    matches, _ambiguous = matching_rows(item, deposit)
    if row.name not in {candidate.name for candidate in matches}:
        frappe.throw(_("La fila ya no coincide con la identidad de la partida."))
    if money(item.amount_usd) != money(row.amount_usd):
        frappe.throw(_("El importe parcial requiere registrar el remanente antes de conciliar."))
    if frappe.db.exists("CN Remittance Target", {"complementary_item": item.name, "docstatus": ["<", 2]}):
        frappe.throw(_("La partida ya está seleccionada como destino de un depósito."))
    if not str(reason or "").strip() or not commitment_date:
        frappe.throw(_("Indique el motivo y la fecha compromiso de seguimiento."))
    try:
        commitment_date = getdate(commitment_date)
    except (ValueError, TypeError):
        frappe.throw(_("La fecha compromiso de seguimiento no es válida."))
    if reconciliation_date(item, deposit) > getdate():
        frappe.throw(_("La fecha de conciliación no puede ser futura."))
    magnitude = ledger_magnitude(item)
    if magnitude is None or magnitude != money(item.amount_usd):
        frappe.throw(_("El débito del core no tiene una conversión verificable que coincida con la partida."))
    item.update({"category": client_credit.CATEGORY, "review_action": "Saldo a favor del cliente",
        "review_status": "Saldo a favor del cliente identificado", "registered_deposit": deposit.name,
        "credit_client": row.client, "credit_detail_row": row.name, "client_number": row.client_number,
        "credit_assigned_to": frappe.session.user, "credit_commitment_date": commitment_date,
        "credit_treatment": "Pendiente de decisión", "credit_origin_date": credit_origin_date(deposit),
        "reclassification_date": reconciliation_date(item, deposit), "compensation_status": "",
        "compensated_usd": 0, "compensation_pending_usd": 0, "result": "Pendiente"})
    client_credit.validate_client_credit(item)
    fields = ("category", "review_action", "review_status", "registered_deposit", "credit_client",
              "credit_detail_row", "client_number", "client_name", "loan_number", "reference", "deposit_voucher",
              "credit_assigned_to", "credit_commitment_date", "credit_treatment", "credit_origin_date",
              "reclassification_date", "compensation_status", "compensated_usd", "compensation_pending_usd",
              "credit_resolved_usd", "credit_pending_usd", "credit_management_status", "credit_history", "result")
    frappe.db.savepoint("cn_reclassification_link")
    try:
        frappe.db.set_value(item.doctype, item.name, {field: item.get(field) for field in fields})
        mark([item.name], deposits=[deposit.name])
        reconcile_deposit(deposit)
        item.reload()
        row.reload()
        if (item.result != client_credit.RESULT or money(row.pending_usd) != 0
                or row.match_status != "Conciliada" or money(item.credit_pending_usd) != money(item.amount_usd)):
            frappe.throw(_("No fue posible conciliar la fila y el saldo a favor; revise los saldos y períodos."))
        comment = _("Reclasificación {0} vinculada al depósito {1}, fila {2}: US$ {3}. Saldo a favor desde {4}; conciliación desde {5}. Motivo: {6}.").format(
            escape(item.name), escape(deposit.name), escape(row.name), item.amount_usd,
            item.credit_origin_date, item.reclassification_date, escape(str(reason).strip()))
        item.add_comment("Comment", comment)
        deposit.add_comment("Comment", comment)
        return {"item": item.name, "deposit": deposit.name, "detail_row": row.name,
                "credit_pending_usd": item.credit_pending_usd, "credit_origin_date": str(item.credit_origin_date),
                "reconciliation_date": str(item.reclassification_date)}
    except Exception:
        frappe.db.rollback(save_point="cn_reclassification_link")
        frappe.clear_document_cache(item.doctype, item.name)
        frappe.clear_document_cache(deposit.doctype, deposit.name)
        raise
