"""Prepare an explicit deposit destination from an existing complementary item."""
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money

CATEGORIES = {"Cobranza administrativa", "Otros ingresos", "Ajuste de conciliación"}


def _check_item(item):
    item.check_permission("write")
    if (item.docstatus not in (0, 1) or item.category not in CATEGORIES | {"Por clasificar"}
            or item.get("origin_deposit") or item.get("registered_deposit")
            or item.get("registration_exception") or item.get("receivable_origin")
            or item.get("related_application") or item.get("compensations")
            or item.get("generic_distribution")
            or item.docstatus == 1 and item.review_action == "No conciliatoria"):
        frappe.throw(_("Esta partida tiene otro tratamiento o vínculo. Revíselo antes de vincularla a un depósito."))
    if not item.employer or not money(item.amount_usd):
        frappe.throw(_("Complete la empresa y el importe de la partida."))
    if item.accounting_classification in {"Aplicación de pago", "ND de Aplicación de pago"}:
        frappe.throw(_("Este movimiento debe revisarse con Vincular a aplicación."))
    if item.period and frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
        frappe.throw(_("Reabra el período de la partida antes de vincularla."))


def _check_deposit(item, deposit):
    from credinomina_reconciliation.deposit_accounting_sources import assert_usable_cash
    from credinomina_reconciliation.paying_employers import allowed_employers
    deposit.check_permission("write")
    assert_usable_cash(deposit)
    if deposit.docstatus != 1:
        frappe.throw(_("Seleccione un depósito confirmado."))
    if item.employer not in allowed_employers(deposit.employer):
        frappe.throw(_("La empresa de la partida no está autorizada para este depósito."))
    deposit._assert_open_related_periods()


def _capacity(item, deposit):
    from credinomina_reconciliation.complementary_balances import load_balances
    from credinomina_reconciliation.client_credit import load_credits
    from credinomina_reconciliation.remittance_selection import pending_selection
    balance = load_balances([item])[item.name]
    if balance.get("amount_issue") or money(balance["pending_usd"]) < 0:
        frappe.throw(_("Revise el importe y la distribución actual de la partida."))
    paid, planned = defaultdict(lambda: money(0)), defaultdict(lambda: money(0))
    for row in balance["distributions"]:
        paid[row["deposit"]] += abs(money(row["amount_usd"]))
    for row in frappe.get_all("CN Remittance Target", filters={
        "parenttype": "CN Remittance Allocation", "complementary_item": item.name, "docstatus": ["<", 2],
    }, fields=["parent", "amount_usd"], limit_page_length=0):
        planned[row.parent] += abs(money(row.amount_usd))
    reserved = sum_money(max(paid[name], planned[name]) for name in paid.keys() | planned.keys())
    available = max(abs(money(item.amount_usd)) - reserved, money(0))
    credits = sum_money(row.amount_usd for row in load_credits([deposit.name]))
    cash = pending_selection([], [deposit.as_dict()], deposit.name, money(deposit.amount_usd) - credits,
                             [row.as_dict() for row in deposit.targets or []])
    return available, money(cash["available_cents"]) / 100, bool(paid[deposit.name] or planned[deposit.name])


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def deposit_query(doctype, txt, searchfield, start, page_len, filters):
    from credinomina_reconciliation.paying_employers import load_payer_map
    item = frappe.get_doc("CN Complementary Item", (filters or {}).get("item_name"))
    _check_item(item)
    payers = {item.employer} | {payer for payer, companies in load_payer_map().items() if item.employer in companies}
    rows = frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1, "employer": ["in", sorted(payers)],
        "consolidated_deposit": ["is", "not set"]},
        or_filters=[[field, "like", "%" + txt + "%"] for field in ("name", "deposit_reference", "deposit_voucher")],
        fields=["name", "deposit_date", "deposit_reference", "amount_usd", "employer"],
        order_by="deposit_date desc, name desc", limit_start=int(start), limit_page_length=min(int(page_len), 50))
    return [(row.name, str(row.deposit_date), row.deposit_reference, row.amount_usd, row.employer) for row in rows]


@frappe.whitelist()
def get_context(item_name: str, deposit_name: str):
    item = frappe.get_doc("CN Complementary Item", item_name)
    _check_item(item)
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name)
    _check_deposit(item, deposit)
    available, cash, existing = _capacity(item, deposit)
    sign = -1 if money(item.amount_usd) < 0 else 1
    return {"item_modified": str(item.modified), "deposit_modified": str(deposit.modified),
            "available_usd": money_float(available), "cash_usd": money_float(cash), "existing": existing,
            "suggested_usd": money_float(sign * (min(available, cash) if sign > 0 else available)),
            "deposit_usd": deposit.amount_usd, "reference": deposit.deposit_reference,
            "date": str(deposit.deposit_date), "employer": deposit.employer,
            "rows": [{"name": row.name, "idx": row.idx, "client_name": row.client_name,
                      "client_number": row.client_number, "loan_number": row.loan_number,
                      "amount_usd": row.amount_usd} for row in deposit.detail_rows or []]}


@frappe.whitelist(methods=["POST"])
def link(item_name: str, deposit_name: str, item_modified: str, deposit_modified: str,
         amount_usd: float, detail_row: str = "", category: str = "", subcategory: str = "", reviewed: int = 0):
    from frappe.utils import cint
    from credinomina_reconciliation.client_credit import lock_credit_deposit
    deposit = lock_credit_deposit(deposit_name)
    item = frappe.get_doc("CN Complementary Item", item_name, for_update=True)
    _check_item(item)
    _check_deposit(item, deposit)
    if str(item.modified) != item_modified or str(deposit.modified) != deposit_modified:
        frappe.throw(_("La partida o el depósito cambió. Recargue y vuelva a seleccionar el depósito."))
    available, cash, existing = _capacity(item, deposit)
    if existing:
        frappe.throw(_("La partida ya está vinculada a este depósito. Abra el depósito para revisar o modificar su destino."))
    amount = money(amount_usd)
    if not amount or abs(amount) > available or (amount > 0 and amount > cash):
        frappe.throw(_("El importe supera el disponible de la partida o del depósito. Revise los saldos mostrados."))
    row = next((row for row in deposit.detail_rows or [] if row.name == detail_row), None)
    if detail_row and not row:
        frappe.throw(_("La fila seleccionada no pertenece al depósito."))
    if item.docstatus == 0:
        item.check_permission("submit")
        if category not in CATEGORIES or not cint(reviewed):
            frappe.throw(_("Seleccione el concepto y confirme que revisó el importe y su signo."))
        item.category = category
        item.subcategory = subcategory or None
        item.review_action = "Partida de depósito"
        item.amount_reviewed = 1
        item.reference = deposit.deposit_reference
        item.amount = money_float(abs(money(item.amount)) * (-1 if amount < 0 else 1))
        item.flags.defer_reconciliation = True
        item.save()
        item.submit()
    elif amount * money(item.amount_usd) <= 0:
        frappe.throw(_("El importe debe conservar el signo de la partida confirmada."))
    target = deposit.append("targets", {"complementary_item": item.name, "amount_usd": money_float(amount),
        "detail_row": detail_row or "", "employer": item.employer,
        "notes": item.description, "detail_row_label": (
            _("Fila {0} · {1}").format(row.source_row or row.idx, row.client_name) if row else "")})
    deposit.detail_status = _("Revisar destinos; partida vinculada, pendiente de conciliación.")
    deposit.save()
    message = _("Vinculación preparada: partida {0}, depósito {1}, importe US$ {2}. Use Conciliar en el depósito para validar y aplicar la distribución.").format(item.name, deposit.name, amount)
    item.add_comment("Comment", message)
    deposit.add_comment("Comment", message)
    return {"deposit": deposit.name, "target": target.name, "amount_usd": money_float(amount)}
