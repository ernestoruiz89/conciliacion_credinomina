import frappe
from frappe.model.document import Document


class CNReconciliationTrace(Document):
    def validate(self):
        if not self.is_new():
            frappe.throw("La trazabilidad de conciliación no se puede modificar.")

    def on_trash(self):
        frappe.throw("La trazabilidad de conciliación no se puede eliminar.")
