import re
import unicodedata

import frappe
from frappe import _
from frappe.model.document import Document


class CNBankAccount(Document):
    def validate(self):
        self.account_name = (self.account_name or "").strip()
        self.bank_name = (self.bank_name or "").strip()
        self.account_number = (self.account_number or "").strip()
        if not self.account_name:
            frappe.throw(_("Indique el nombre de la cuenta bancaria."))
        previous = self.get_doc_before_save()
        if (
            previous and previous.name == previous.account_name
            and self.name != self.account_name
        ):
            frappe.throw(_("Para cambiar el nombre de la cuenta, use Renombrar."))
        if self.account_name == "NO IDENTIFICADA":
            return  # A holding account must not require fictitious bank data.
        if not self.bank_name:
            frappe.throw(_("Indique el nombre del banco."))
        if not self.account_number:
            frappe.throw(_("Indique el número de la cuenta bancaria."))
        if self.currency not in {"USD", "NIO"}:
            frappe.throw(_("La moneda de la cuenta debe ser USD o NIO."))

    def before_rename(self, old, new, merge=False):
        new = (new or "").strip()
        if old == "NO IDENTIFICADA" or new == "NO IDENTIFICADA":
            frappe.throw(_("NO IDENTIFICADA es una cuenta provisional. Seleccione la cuenta correcta en cada depósito; no renombre ni fusione este marcador."))
        if merge:
            # A merge deletes the source. Check before Frappe moves any links.
            self.check_permission("write")
            self.check_permission("delete")
            target = frappe.get_doc(self.doctype, new)
            target.check_permission("write")
            bank = lambda value: " ".join((value or "").split()).casefold()
            if not bank(self.bank_name) or bank(self.bank_name) != bank(target.bank_name):
                frappe.throw(_("Solo se pueden fusionar cuentas del mismo banco. Revise el nombre del banco en ambas cuentas."))
            if self.currency not in {"USD", "NIO"} or self.currency != target.currency:
                frappe.throw(_("Solo se pueden fusionar cuentas con la misma moneda."))
            if not _account_key(self.account_number) or _account_key(self.account_number) != _account_key(target.account_number):
                frappe.throw(_("Solo se pueden fusionar cuentas con el mismo número completo de cuenta bancaria. No basta con que coincidan los últimos dígitos."))
        return new

    def after_rename(self, old, new, merge=False):
        if merge:
            # Keep all destination metadata; Frappe transfers links and audit history.
            return
        frappe.db.set_value(self.doctype, new, "account_name", new, update_modified=False)
        self.account_name = new


def _account_key(value):
    # Preserve leading zeros and every significant character: never suffix-match.
    return re.sub(r"[\s\-]+", "", unicodedata.normalize("NFKC", value or "")).casefold()
