"""Explicit company difference rows: documentary until backed by imported core."""
import hashlib
import json
from contextvars import ContextVar

import frappe
from frappe import _
from frappe.utils import now_datetime, escape_html, cint

from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.remittance_detail import detail_amount_usd

KIND = "Diferencia documentada"
SETTLED = "Conciliado con diferencia documentada"
WRITING = ContextVar("deposit_difference_writing", default=False)
FIELDS = ("row_kind", "difference_amount_usd", "difference_basis", "difference_core_item",
          "difference_recorded_by", "difference_recorded_on", "employer", "client_name", "client",
          "client_number", "loan_number", "employee_number", "national_id", "deducted_usd", "deducted_nio")


def is_difference(row):
    return row.get("row_kind") == KIND


def basis(document, rows):
    ordinary = [row for row in rows if not is_difference(row)]
    data = [str(document.get(field) or "") for field in
            ("employer", "detail_hash", "detail_source_file", "detail_file")]
    data += [str(money(document.get("amount_usd"))), str(document.get("fx_rate") or 0)]
    data += sorted((str(row.get("name") or ""), str(money(row.get("deducted_usd"))),
                    str(money(row.get("deducted_nio")))) for row in ordinary)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def detail_difference(document, rows=None):
    rows = document.get("detail_rows") if rows is None else rows
    ordinary = [row for row in rows or [] if not is_difference(row)]
    if not ordinary:
        frappe.throw(_("Cargue primero las filas de clientes del detalle."))
    total = money(0)
    for row in ordinary:
        value, reason = detail_amount_usd(row, float(document.get("fx_rate") or 0))
        if not value and reason != "No deducido":
            frappe.throw(_("Revise los importes del detalle: {0}").format(reason))
        total += money(value)
    return money(document.get("amount_usd")) - total


def guard_rows(document, previous):
    if WRITING.get():
        return
    def recorded(doc):
        return [(row.get("name"), tuple(str(row.get(key) or "") for key in FIELDS))
                for row in (doc.get("detail_rows") or []) if is_difference(row)] if doc else []
    if recorded(document) != recorded(previous):
        frappe.throw(_("Use Clasificar / documentar diferencia para registrar o cambiar la fila de empresa."))


def validate_core(item, employer, amount):
    item.check_permission("read")
    excluded = {"Vinculado a depósito", "Ajuste de aplicación", "Compensación entre partidas",
                "Diferencia por tolerancia", "Saldo a favor del cliente", "Saldo a favor de la empresa"}
    if (item.docstatus != 1 or not item.get("accounting_source_key") or item.get("origin_deposit")
            or item.get("category") in excluded or item.get("employer") != employer
            or money(item.get("amount_usd")) != money(amount)):
        frappe.throw(_("Elija una partida del core confirmada, de la misma empresa y con el mismo importe y signo de la diferencia, disponible como destino."))
    if item.get("period") and frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
        frappe.throw(_("La partida pertenece a un período cerrado."))


@frappe.whitelist()
def preview(name):
    doc = frappe.get_doc("CN Remittance Allocation", name)
    doc.check_permission("read")
    row = next((row for row in doc.detail_rows if is_difference(row)), None)
    return dict(modified=str(doc.modified), employer=doc.employer,
                amount_usd=money_float(detail_difference(doc)),
                difference_reason=(row or doc).get("difference_reason"),
                difference_notes=(row or doc).get("difference_notes"),
                core_item=row.get("difference_core_item") if row else "", has_difference=bool(row))


@frappe.whitelist(methods=["POST"])
def record(name, modified, difference_reason="", difference_notes="", core_item=None, remove=0):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    doc = frappe.get_doc("CN Remittance Allocation", name)
    doc.check_permission("write")
    lock_cash_pool(reconciliation_companies(doc.employer))
    frappe.db.get_value(doc.doctype, name, "name", for_update=True)
    doc.reload()
    doc.check_permission("write")
    if doc.docstatus == 2 or str(doc.modified) != str(modified):
        frappe.throw(_("El depósito cambió o está cancelado. Recargue antes de documentar."))
    doc._assert_can_reverse_distribution()
    if cint(remove):
        removed = [row.as_dict() for row in doc.detail_rows if is_difference(row)]
        doc.set("detail_rows", [row for row in doc.detail_rows if not is_difference(row)])
        token = WRITING.set(True)
        try:
            doc.save()
        finally:
            WRITING.reset(token)
        frappe.db.set_value(doc.doctype, doc.name, {"result": "Pendiente", "financial_status": "Pendiente",
                            "detail_status": "Cargado; pendiente de conciliación"}, update_modified=False)
        doc.add_comment("Comment", _("Diferencia de empresa retirada; requiere conciliar. Evidencia anterior: {0}").format(
            escape_html(frappe.as_json(removed))))
        return dict(removed=True)
    amount = detail_difference(doc)
    if not amount:
        frappe.throw(_("El depósito y el detalle ya coinciden; no hay diferencia que registrar."))
    if not difference_reason or not str(difference_notes or "").strip():
        frappe.throw(_("Indique la razón y la documentación de la diferencia."))
    frappe.get_doc("CN Difference Reason", difference_reason).check_permission("read")
    if core_item:
        validate_core(frappe.get_doc("CN Complementary Item", core_item), doc.employer, amount)
    rows = [row for row in doc.detail_rows if is_difference(row)]
    if len(rows) > 1:
        frappe.throw(_("Hay más de una fila de diferencia; revise el detalle."))
    row = rows[0] if rows else doc.append("detail_rows", {})
    before = {key: row.get(key) for key in (*FIELDS, "difference_reason", "difference_notes")}
    row.update(dict(row_kind=KIND, employer=doc.employer, client_name=doc.employer,
                    client="", client_number="", loan_number="", employee_number="", national_id="",
                    deducted_usd=0, deducted_nio=0, difference_amount_usd=money_float(amount),
                    amount_usd=money_float(amount), linked_usd=0, pending_usd=money_float(amount),
                    difference_reason=difference_reason, difference_notes=str(difference_notes).strip(),
                    difference_core_item=core_item or "", difference_core_pending_usd=abs(money_float(amount)),
                    difference_recorded_by=frappe.session.user, difference_recorded_on=now_datetime(),
                    difference_core_status="Vinculada; pendiente de conciliar" if core_item else "Pendiente de respaldo del core",
                    match_status="Pendiente", match_reason="Diferencia de empresa; pendiente de conciliación",
                    matched_targets="[]", matched_targets_summary=""))
    row.difference_basis = basis(doc, doc.detail_rows)
    token = WRITING.set(True)
    try:
        doc.save()
    finally:
        WRITING.reset(token)
    # Save changes only the instructions; reconciliation is an explicit action.
    frappe.db.set_value(doc.doctype, doc.name, {"result": "Pendiente", "financial_status": "Pendiente",
                        "detail_status": "Cargado; pendiente de conciliación"}, update_modified=False)
    after = {key: row.get(key) for key in (*FIELDS, "difference_reason", "difference_notes")}
    doc.add_comment("Comment", _("Diferencia de empresa registrada. Antes: {0}. Después: {1}. Requiere conciliar.").format(
        escape_html(frappe.as_json(before)), escape_html(frappe.as_json(after))))
    return dict(row=row.name, amount_usd=money_float(amount))


def prepare(document, rows, claims, instructions):
    """Validate the accepted snapshot before it can unblock a cash distribution."""
    differences = [row for row in rows if is_difference(row)]
    if not differences:
        return None
    row = differences[0]
    state = dict(row=row, amount=money(row.get("difference_amount_usd")), error="", core=None)
    if (len(differences) != 1 or row.get("difference_basis") != basis(document, rows)
            or not row.get("difference_reason") or not row.get("difference_notes")
            or state["amount"] != detail_difference(document, rows)):
        state["error"] = "La diferencia documentada cambió; vuelva a clasificar / documentar antes de conciliar"
    elif instructions:
        state["error"] = "Revise los destinos manuales: la diferencia de empresa requiere distribución por detalle sin destinos duplicados"
    elif row.get("difference_core_item"):
        claim = next((claim for claim in claims if claim["id"] == "X:" + row["difference_core_item"]), None)
        if (not claim or not claim.get("accounting_source_key") or claim.get("group") != document.employer
                or claim.get("period_closed") or money(claim["amount_usd"]) * state["amount"] <= 0
                or abs(money(claim["amount_usd"])) < abs(state["amount"])):
            state["error"] = "La partida del core no está disponible por el importe, signo y empresa requeridos"
        else:
            state["core"] = claim
    return state


def cash_budgets(plans, difference):
    """Largest remainders, in cents: preserve total cash without favoring the last client."""
    amounts = [int(money(plan["amount_usd"]) * 100) for plan in plans]
    total = sum(amounts)
    shortage = -int(money(difference) * 100)
    if not total or shortage <= 0:
        return [money_float(money(plan["amount_usd"])) for plan in plans]
    available = max(total - shortage, 0)
    cents = [amount * available // total for amount in amounts]
    order = sorted(range(len(plans)), key=lambda i: (-(amounts[i] * available % total), str(plans[i]["row"].name)))
    for index in order[:available - sum(cents)]:
        cents[index] += 1
    return [value / 100 for value in cents]
