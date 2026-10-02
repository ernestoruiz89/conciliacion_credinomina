import frappe
from frappe.model.document import Document


class CNAccountingBatch(Document):
    def validate(self):
        if not self.flags.accounting_batch_internal:
            frappe.throw("Este registro se administra desde Carga masiva.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("El historial de la carga masiva no se elimina desde el formulario.")
