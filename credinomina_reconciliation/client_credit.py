"""Client-owned excess cash: classification is separate from subsequent management."""
import json
from html import escape

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from credinomina_reconciliation.parsers import clean_text, normalize_credit_number
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money

CATEGORY = "Saldo a favor del cliente"
RESULT = "Saldo a favor documentado"
FINANCIAL_FIELDS = (
    "category", "amount", "currency", "fx_rate", "amount_usd", "employer", "period",
    "registered_deposit", "credit_client", "client_number", "loan_number", "credit_detail_row", "posting_date",
)
MANAGED_FIELDS = ("credit_resolved_usd", "credit_pending_usd", "credit_management_status", "credit_history")


def _same_value(field, before, after):
    if field in {"amount", "amount_usd", "deposit_amount", "deducted_usd", "deducted_nio"}:
        return money(before) == money(after)
    if field == "fx_rate":
        return decimal_value(before) == decimal_value(after)
    return str(before or "") == str(after or "")


def _assert_financial_identity(doc, previous):
    if any(not _same_value(field, previous.get(field), doc.get(field)) for field in FINANCIAL_FIELDS):
        frappe.throw(_("No puede cambiar los datos financieros de un saldo a favor confirmado. Conserve el depósito, cliente, empresa, crédito, fila, fecha e importe originales."))


def row_credit_amounts(items, rows):
    """Exact child IDs only: never distribute an unidentified excess by name."""
    known = {row.get("name") for row in rows}
    by_row = {}
    for item in items:
        key = item.get("credit_detail_row")
        if key and key in known:
            by_row.setdefault(key, []).append(item)
    return {key: money_float(sum_money(item.get("amount_usd") for item in values))
            for key, values in by_row.items()}


def load_credits(deposit_names):
    if not deposit_names:
        return []
    return frappe.get_all("CN Complementary Item", filters={
        "docstatus": 1, "category": CATEGORY, "registered_deposit": ["in", list(deposit_names)],
    }, fields=["name", "category", "registered_deposit", "credit_detail_row", "credit_client",
               "client_name", "client_number", "loan_number", "employer", "period", "amount_usd",
               "result", "credit_pending_usd", "credit_management_status"], order_by="creation asc", limit_page_length=0)


def validate_client_credit(doc, previous=None):
    from credinomina_reconciliation.company_credit import ensure_related_periods_open
    from credinomina_reconciliation.paying_employers import allowed_employers

    if money(doc.amount_usd) <= 0:
        frappe.throw(_("El saldo a favor del cliente debe ser positivo."))
    if not doc.get("credit_assigned_to") or not doc.get("credit_commitment_date"):
        frappe.throw(_("Indique responsable y fecha compromiso del saldo a favor del cliente."))
    if doc.get("credit_treatment") not in {"Pendiente de decisión", "Devolución", "Aplicación futura"}:
        frappe.throw(_("Seleccione el tratamiento del saldo a favor del cliente."))
    if previous and previous.docstatus == 1:
        _assert_financial_identity(doc, previous)
        for field in MANAGED_FIELDS:
            doc.set(field, previous.get(field))
        return  # Follow-up must retain the original identities even if the client changes employers.
    if not doc.registered_deposit:
        frappe.throw(_("Seleccione el depósito de origen del saldo a favor del cliente."))
    deposit = frappe.get_doc("CN Remittance Allocation", doc.registered_deposit)
    deposit.check_permission("read")
    if deposit.docstatus != 1:
        frappe.throw(_("Confirme primero el depósito de origen."))
    frappe.db.get_value(deposit.doctype, deposit.name, "name", for_update=True)
    other_credits = frappe.get_all("CN Complementary Item", filters={
        "docstatus": 1, "registered_deposit": deposit.name, "name": ["!=", doc.name or ""],
        "category": ["in", [CATEGORY, "Saldo a favor de la empresa"]],
    }, pluck="amount_usd", limit_page_length=0)
    capacity = money(deposit.amount_usd) - money(deposit.allocated_usd) - sum_money(other_credits)
    if money(doc.amount_usd) > capacity:
        frappe.throw(_("El saldo a favor supera el efectivo del depósito sin asignar ni documentar. No puede reclasificar dinero ya aplicado o reservado."))
    if not doc.get("credit_client") and doc.client_number:
        doc.credit_client = frappe.db.get_value("CN Client", {"client_number": doc.client_number}, "name")
    if not doc.get("credit_client"):
        frappe.throw(_("Seleccione el cliente al que pertenece el saldo a favor."))
    client = frappe.get_doc("CN Client", doc.credit_client)
    client.check_permission("read")
    if not client.employer or client.employer not in allowed_employers(deposit.employer):
        frappe.throw(_("El cliente no pertenece a una empresa autorizada por la pagadora."))
    if doc.employer and doc.employer != client.employer:
        frappe.throw(_("La empresa de la partida debe ser la del cliente beneficiario."))
    if doc.client_number and clean_text(doc.client_number) != clean_text(client.client_number):
        frappe.throw(_("El número de cliente no coincide con el cliente seleccionado."))
    doc.employer, doc.client_number, doc.client_name = client.employer, client.client_number, client.client_name
    doc.loan_number = normalize_credit_number(doc.loan_number)
    doc.reference, doc.deposit_voucher = deposit.deposit_reference, deposit.deposit_voucher
    if doc.period and frappe.db.get_value("CN Reconciliation Period", doc.period, "employer") != doc.employer:
        frappe.throw(_("El período debe pertenecer a la empresa del cliente."))
    if not clean_text(doc.description):
        frappe.throw(_("Documente el motivo del saldo a favor del cliente."))

    rows = deposit.detail_rows or []
    row_id = doc.get("credit_detail_row")
    # Optional explicit link: a detail of US$100 may omit the US$10 excess,
    # whereas a detail of US$110 includes it. Never infer the latter by name.
    if row_id:
        row = next((row for row in rows if row.name == row_id), None)
        if not row:
            frappe.throw(_("La fila del saldo a favor no pertenece al depósito seleccionado."))
        if (row.get("client") and row.client != client.name) or (
            row.get("client_number") and clean_text(row.client_number) != clean_text(client.client_number)
        ) or (row.get("employer") and row.employer != client.employer):
            frappe.throw(_("La fila del depósito pertenece a otro cliente o empresa."))
        if doc.loan_number and row.loan_number and normalize_credit_number(row.loan_number) != doc.loan_number:
            frappe.throw(_("El crédito no coincide con la fila del depósito."))
        doc.loan_number = doc.loan_number or normalize_credit_number(row.loan_number)
        from credinomina_reconciliation.remittance_detail import detail_amount_usd
        existing = [item for item in load_credits([deposit.name]) if item.name != doc.name and item.credit_detail_row == row_id]
        available = (money(detail_amount_usd(row, deposit.fx_rate)[0])
                     - sum_money(item.amount_usd for item in existing) - max(money(row.get("linked_usd")), money(0)))
        if money(doc.amount_usd) > available:
            frappe.throw(_("El saldo a favor supera el importe de la fila del cliente disponible para clasificar."))

    doc.credit_resolved_usd = 0
    doc.credit_pending_usd = money_float(doc.amount_usd)
    doc.credit_management_status = "Pendiente"
    doc.credit_history = "[]"
    ensure_related_periods_open(doc)


def guard_credit_category(doc, previous):
    if previous and previous.docstatus == 1 and previous.category == CATEGORY and doc.category != CATEGORY:
        frappe.throw(_("No puede reclasificar un saldo a favor del cliente confirmado."))
    if previous and previous.category == CATEGORY and previous.docstatus == 1:
        for field in MANAGED_FIELDS:
            doc.set(field, previous.get(field))


def guard_cancel(doc):
    stored = frappe.db.get_value(doc.doctype, doc.name, [*FINANCIAL_FIELDS, "credit_resolved_usd"], as_dict=True) if doc.get("doctype") and doc.get("name") else None
    if stored and stored.category == CATEGORY:
        _assert_financial_identity(doc, stored)
    if (stored and stored.category == CATEGORY and money(stored.credit_resolved_usd)) or (doc.category == CATEGORY and money(doc.get("credit_resolved_usd"))):
        frappe.throw(_("No se puede cancelar un saldo a favor con devoluciones o aplicaciones documentadas. Revise la gestión registrada."))


def guard_detail_replacement(deposit, operation="reemplazar el detalle"):
    if deposit.name and deposit.docstatus != 0 and load_credits([deposit.name]):
        frappe.throw(_("No puede {0}: el depósito tiene saldos a favor de clientes confirmados. Cancele primero las partidas sin gestión si necesita corregirlo.").format(operation))


def guard_deposit_changes(deposit, previous):
    if not previous:
        return
    credits = load_credits([deposit.name])
    if not credits:
        return
    for field in ("employer", "deposit_date", "deposit_currency", "deposit_amount", "fx_rate", "deposit_reference", "deposit_voucher"):
        if not _same_value(field, previous.get(field), deposit.get(field)):
            frappe.throw(_("El depósito tiene saldos a favor de clientes confirmados. Conserve los datos de origen del depósito."))
    by_id = {row.name: row for row in deposit.detail_rows}
    old = {row.name: row for row in previous.detail_rows}
    for credit in credits:
        key = credit.credit_detail_row
        if not key:
            continue
        def changed(field):
            return not _same_value(field, old[key].get(field), by_id[key].get(field))
        if key not in by_id or key not in old or any(changed(field)
                for field in ("client", "client_number", "loan_number", "employer", "amount_usd", "deducted_usd", "deducted_nio")):
            frappe.throw(_("No cambie ni elimine una fila con saldo a favor del cliente confirmado. Conserve la evidencia de su clasificación."))


@frappe.whitelist(methods=["POST"])
def record_management(item_name, modified, treatment, amount_usd, event_date, reference, support_file, notes=""):
    """Document proven external management; never post GL, create a payment or reallocate cash."""
    frappe.db.get_value("CN Complementary Item", item_name, "name", for_update=True)
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("write")
    item.check_permission("submit")
    if item.docstatus != 1 or item.category != CATEGORY or item.result != RESULT:
        frappe.throw(_("Confirme y documente primero el saldo a favor del cliente."))
    if str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de registrar la gestión."))
    amount = money(amount_usd)
    pending = money(item.amount_usd) - money(item.get("credit_resolved_usd"))
    if amount <= 0 or amount > pending:
        frappe.throw(_("El importe de la gestión debe ser positivo y no superar el saldo pendiente."))
    if treatment not in {"Devolución", "Aplicación futura"} or not clean_text(reference) or not support_file or not event_date:
        frappe.throw(_("Indique devolución o aplicación, fecha, referencia del core/comprobante y soporte."))
    if getdate(event_date) < getdate(item.posting_date) or getdate(event_date) > getdate():
        frappe.throw(_("La fecha de gestión no puede ser futura ni anterior al saldo a favor."))
    files = frappe.get_all("File", filters={"file_url": support_file, "attached_to_doctype": item.doctype,
                                           "attached_to_name": item.name}, pluck="name", limit_page_length=1)
    if not files:
        frappe.throw(_("El soporte de la gestión debe estar adjunto a esta partida."))
    frappe.get_doc("File", files[0]).check_permission("read")
    history = json.loads(item.get("credit_history") or "[]")
    history.append({"tratamiento": treatment, "importe_usd": money_float(amount), "fecha": str(getdate(event_date)),
                    "referencia": clean_text(reference), "soporte": support_file, "observaciones": clean_text(notes),
                    "usuario": frappe.session.user, "registrado_el": str(now_datetime())})
    resolved = money(item.get("credit_resolved_usd")) + amount
    status = "Resuelto" if resolved == money(item.amount_usd) else "Parcialmente resuelto"
    frappe.db.set_value(item.doctype, item.name, {
        "credit_resolved_usd": money_float(resolved), "credit_pending_usd": money_float(money(item.amount_usd) - resolved),
        "credit_management_status": status, "credit_history": json.dumps(history, ensure_ascii=False),
    })
    item.add_comment("Info", _("Gestión de saldo a favor: {0}; US$ {1}; referencia {2}.").format(treatment, amount, escape(clean_text(reference))))
    item.clear_cache()
    return {"name": item.name, "status": status, "pending_usd": money_float(money(item.amount_usd) - resolved)}


@frappe.whitelist()
def get_deposit_detail(deposit_name):
    """Permissions-checked row selector for an explicit client-owned excess."""
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
    deposit.check_permission("read")
    from credinomina_reconciliation.paying_employers import allowed_employers
    return {"employers": sorted(allowed_employers(deposit.employer)), "rows": [
        {field: row.get(field) for field in ("name", "client", "client_number", "client_name", "loan_number", "employer", "source_row", "amount_usd", "pending_usd")}
        for row in deposit.detail_rows]}
