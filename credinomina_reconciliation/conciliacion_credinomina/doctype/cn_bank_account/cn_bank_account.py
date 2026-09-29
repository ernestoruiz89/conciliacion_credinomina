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
        if not self.bank_name:
            frappe.throw(_("Indique el nombre del banco."))
        if not self.account_number:
            frappe.throw(_("Indique el número de la cuenta bancaria."))
        if self.currency not in {"USD", "NIO"}:
            frappe.throw(_("La moneda de la cuenta debe ser USD o NIO."))

    def before_rename(self, old, new, merge=False):
        if merge:
            frappe.throw(_("No se pueden fusionar cuentas bancarias Credinómina."))
        return (new or "").strip()

    def after_rename(self, old, new, merge=False):
        frappe.db.set_value(self.doctype, new, "account_name", new, update_modified=False)
        self.account_name = new
