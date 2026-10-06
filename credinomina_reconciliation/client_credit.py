"""Client-owned excess cash: classification is separate from subsequent management."""
import json
import hashlib
from uuid import uuid4
from html import escape

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from credinomina_reconciliation.parsers import clean_text, normalize_credit_number
from credinomina_reconciliation.rounding import decimal_value, money, money_float, sum_money

CATEGORY = "Saldo a favor del cliente"
CREDIT_CATEGORIES = (CATEGORY, "Saldo a favor de la empresa")
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
    """All confirmed cash reservations, including externally managed balances.

    A refund/future application never makes the original deposit spendable again.
    Company credits have no detail row and cannot reduce a client's row.
    """
    if not deposit_names:
        return []
    return frappe.get_all("CN Complementary Item", filters={
        "docstatus": 1, "category": ["in", CREDIT_CATEGORIES], "registered_deposit": ["in", list(deposit_names)],
    }, fields=["name", "category", "registered_deposit", "credit_detail_row", "credit_client",
               "client_name", "client_number", "loan_number", "employer", "period", "amount_usd",
               "result", "credit_pending_usd", "credit_management_status"], order_by="creation asc", limit_page_length=0)


def lock_credit_deposit(deposit_name):
    """Use the reconciliation lock order, then a current (not snapshot) read."""
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    employer = frappe.db.get_value("CN Remittance Allocation", deposit_name, "employer")
    if employer:
        lock_cash_pool(reconciliation_companies(employer))
    return frappe.get_doc("CN Remittance Allocation", deposit_name, for_update=True)


def other_reservations(deposit_name, item_name):
    # A concurrent confirmation may have committed after this transaction's
    # first read. FOR UPDATE avoids a stale repeatable-read capacity check.
    return frappe.db.sql("""select name, amount_usd, credit_detail_row
        from `tabCN Complementary Item`
        where registered_deposit=%s and docstatus=1 and name!=%s
        and category in ('Saldo a favor del cliente', 'Saldo a favor de la empresa')
        for update""", (deposit_name, item_name or ""), as_dict=True)


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
    deposit = lock_credit_deposit(doc.registered_deposit)
    deposit.check_permission("read")
    if deposit.docstatus != 1:
        frappe.throw(_("Confirme primero el depósito de origen."))
    other_credits = other_reservations(deposit.name, doc.name)
    capacity = money(deposit.amount_usd) - money(deposit.allocated_usd) - sum_money(row.amount_usd for row in other_credits)
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
        existing = [item for item in other_credits if item.credit_detail_row == row_id]
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
    if previous and previous.docstatus == 1 and previous.category in CREDIT_CATEGORIES and doc.category != previous.category:
        frappe.throw(_("No puede reclasificar un saldo a favor confirmado. Cancele primero la partida sin gestiones y documente el motivo."))
    if previous and previous.category in CREDIT_CATEGORIES and previous.docstatus == 1:
        _assert_financial_identity(doc, previous)
        for field in MANAGED_FIELDS:
            doc.set(field, previous.get(field))


def guard_cancel(doc):
    stored = frappe.db.get_value(doc.doctype, doc.name, [*FINANCIAL_FIELDS, "credit_resolved_usd", "credit_history"], as_dict=True) if doc.get("doctype") and doc.get("name") else None
    if stored and stored.category in CREDIT_CATEGORIES:
        _assert_financial_identity(doc, stored)
    if any(item and item.category in CREDIT_CATEGORIES and
           (money(item.get("credit_resolved_usd")) or json.loads(item.get("credit_history") or "[]"))
           for item in (stored, doc)):
        frappe.throw(_("No se puede cancelar un saldo a favor con devoluciones o aplicaciones documentadas. Revise la gestión registrada."))


def guard_detail_replacement(deposit, operation="reemplazar el detalle"):
    if deposit.name and deposit.docstatus != 0 and load_credits([deposit.name]):
        frappe.throw(_("No puede {0}: el depósito tiene saldos a favor confirmados. Cancele primero las partidas sin gestión si necesita corregirlo.").format(operation))


def guard_deposit_changes(deposit, previous):
    if not previous:
        return
    credits = load_credits([deposit.name])
    if not credits:
        return
    for field in ("employer", "deposit_date", "deposit_currency", "deposit_amount", "fx_rate", "deposit_reference", "deposit_voucher"):
        if not _same_value(field, previous.get(field), deposit.get(field)):
            frappe.throw(_("El depósito tiene saldos a favor confirmados. Conserve los datos de origen del depósito."))
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
def record_management(item_name, modified, treatment, amount_usd, event_date, reference, support_file="", notes=""):
    """Document proven external management; never post GL, create a payment or reallocate cash."""
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if item.docstatus != 1 or item.category not in CREDIT_CATEGORIES or item.result != RESULT:
        frappe.throw(_("Confirme y documente primero el saldo a favor."))
    if str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de registrar la gestión."))
    amount = money(amount_usd)
    pending = money(item.amount_usd) - money(item.get("credit_resolved_usd"))
    if amount <= 0 or amount > pending:
        frappe.throw(_("El importe de la gestión debe ser positivo y no superar el saldo pendiente."))
    if treatment not in {"Devolución", "Aplicación futura"} or not clean_text(reference) or not event_date:
        frappe.throw(_("Indique devolución o aplicación, fecha y referencia del core/comprobante."))
    if getdate(event_date) < getdate(item.posting_date) or getdate(event_date) > getdate():
        frappe.throw(_("La fecha de gestión no puede ser futura ni anterior al saldo a favor."))
    support_file = clean_text(support_file)
    if support_file:
        files = frappe.get_all("File", filters={"file_url": support_file, "attached_to_doctype": item.doctype,
                                               "attached_to_name": item.name}, pluck="name", limit_page_length=1)
        if not files:
            frappe.throw(_("El soporte de la gestión debe estar adjunto a esta partida."))
        frappe.get_doc("File", files[0]).check_permission("read")
    history = json.loads(item.get("credit_history") or "[]")
    history.append({"operation_id": uuid4().hex, "tratamiento": treatment, "importe_usd": money_float(amount), "fecha": str(getdate(event_date)),
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


def management_entry_id(entry, index):
    # Legacy entries keep their original bytes/fields. Their identity is derived,
    # not retroactively written into the financial history.
    return entry.get("operation_id") or "legacy-" + hashlib.sha256(
        json.dumps([index, entry], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def management_history(item):
    history = json.loads(item.get("credit_history") or "[]")
    reversed_ids = {entry.get("reverses") for entry in history if entry.get("reverses")}
    return [{**entry, "entry_id": management_entry_id(entry, index),
             "can_reverse": bool(not entry.get("reverses") and money(entry.get("importe_usd")) > 0
                                 and management_entry_id(entry, index) not in reversed_ids)}
            for index, entry in enumerate(history)]


@frappe.whitelist()
def get_management_history(item_name):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("read")
    return {"modified": str(item.modified), "rows": management_history(item)}


@frappe.whitelist(methods=["POST"])
def reverse_management(item_name, modified, entry_id, event_date, reason):
    """Correct recorded external management; never refund or release bank cash."""
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    item.check_permission("write")
    item.check_permission("submit")
    if item.docstatus != 1 or item.category not in CREDIT_CATEGORIES or item.result != RESULT:
        frappe.throw(_("Seleccione un saldo a favor confirmado."))
    if str(item.modified) != str(modified):
        frappe.throw(_("La partida cambió. Recárguela antes de revertir la gestión."))
    reason = clean_text(reason)
    if not reason or not event_date:
        frappe.throw(_("Indique fecha y motivo de la reversión."))
    entries = management_history(item)
    original = next((entry for entry in entries if entry["entry_id"] == entry_id), None)
    if not original or not original["can_reverse"]:
        frappe.throw(_("La gestión no existe o ya fue revertida."))
    date = getdate(event_date)
    earliest = max([getdate(item.posting_date), *[getdate(entry["fecha"]) for entry in entries if entry.get("fecha")]])
    if date < earliest or date > getdate():
        frappe.throw(_("La reversión no puede ser futura ni anterior a las gestiones registradas."))
    amount = money(original["importe_usd"])
    resolved = money(item.get("credit_resolved_usd")) - amount
    if resolved < 0 or resolved > money(item.amount_usd):
        frappe.throw(_("El saldo gestionado no coincide con el historial. Revise la partida antes de revertir."))
    history = json.loads(item.credit_history or "[]")
    history.append({"operation_id": uuid4().hex, "reverses": entry_id, "tratamiento": "Reversión de gestión",
                    "importe_usd": -money_float(amount), "fecha": str(date), "referencia": original.get("referencia") or "",
                    "soporte": original.get("soporte") or "", "observaciones": reason,
                    "usuario": frappe.session.user, "registrado_el": str(now_datetime())})
    pending = money(item.amount_usd) - resolved
    status = "Pendiente" if not resolved else "Parcialmente resuelto"
    frappe.db.set_value(item.doctype, item.name, {
        "credit_resolved_usd": money_float(resolved), "credit_pending_usd": money_float(pending),
        "credit_management_status": status, "credit_history": json.dumps(history, ensure_ascii=False)})
    item.add_comment("Info", _("Gestión revertida: {0}; US$ {1}; motivo: {2}. La distribución del depósito se conserva.").format(
        escape(entry_id), amount, escape(reason)))
    item.clear_cache()
    return {"name": item.name, "status": status, "pending_usd": money_float(pending)}


@frappe.whitelist()
def get_deposit_detail(deposit_name):
    """Permissions-checked row selector for an explicit client-owned excess."""
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
    deposit.check_permission("read")
    from credinomina_reconciliation.paying_employers import allowed_employers
    return {"employers": sorted(allowed_employers(deposit.employer)), "rows": [
        {field: row.get(field) for field in ("name", "client", "client_number", "client_name", "loan_number", "employer", "source_row", "amount_usd", "pending_usd")}
        for row in deposit.detail_rows]}
