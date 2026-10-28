"""Validation for matching a core reclassification to a deposit detail."""
import frappe
from frappe import _

from credinomina_reconciliation.client_identity import name_key
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier
from credinomina_reconciliation.rounding import money
from frappe.utils import getdate


def reconciliation_date(item, deposit):
    """The reconciliation cannot precede either original event."""
    if not item.get("posting_date") or not deposit.get("deposit_date"):
        frappe.throw(_("El depósito y la partida deben tener fecha para determinar la conciliación."))
    return max(getdate(item.get("posting_date")), getdate(deposit.get("deposit_date")))


def credit_origin_date(deposit):
    if not deposit.get("deposit_date"):
        frappe.throw(_("El depósito debe tener fecha para determinar el saldo a favor."))
    return getdate(deposit.get("deposit_date"))


def validate_pair(item, deposit, row):
    """Reject uncertain or contradictory evidence before any financial write."""
    if (item.get("docstatus") != 1 or item.get("category") != "Compensación entre partidas"
            or not item.get("accounting_source_key") or money(item.get("source_debit")) <= 0
            or money(item.get("source_credit")) != 0):
        frappe.throw(_("Seleccione una partida confirmada del core con débito y sin crédito."))
    if (item.get("origin_deposit") or item.get("registered_deposit") or item.get("related_application")
            or item.get("registration_exception") or item.get("receivable_origin")
            or item.get("compensations") or item.get("generic_distribution")):
        frappe.throw(_("La partida ya tiene un vínculo o tratamiento incompatible."))
    if deposit.get("docstatus") != 1 or row.get("parent") != deposit.get("name"):
        frappe.throw(_("Seleccione una fila de un depósito confirmado."))
    if not row.get("client") or not item.get("employer") or item.get("employer") != row.get("employer"):
        frappe.throw(_("La empresa y el cliente identificado deben coincidir con la fila del depósito."))
    item_loan, row_loan = canonical_credit_number(item.get("loan_number")), canonical_credit_number(row.get("loan_number"))
    if not item_loan or not row_loan or item_loan != row_loan:
        frappe.throw(_("El número de crédito de la partida debe coincidir con la fila del depósito."))
    item_number = canonical_identifier(item.get("client_number"))
    row_number = canonical_identifier(row.get("client_number"))
    if item_number and item_number != row_number:
        frappe.throw(_("El número de cliente de la partida no coincide con la fila."))
    if not item_number and (not name_key(item.get("client_name"))
                            or name_key(item.get("client_name")) != name_key(row.get("client_name"))):
        frappe.throw(_("El nombre del cliente de la partida no coincide con la fila."))
    if money(item.get("amount_usd")) <= 0 or money(item.get("amount_usd")) != money(row.get("amount_usd")):
        frappe.throw(_("El importe de la partida debe coincidir con el importe de la fila del depósito."))
