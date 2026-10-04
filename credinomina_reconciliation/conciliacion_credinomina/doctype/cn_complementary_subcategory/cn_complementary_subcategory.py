import frappe
from frappe import _
from frappe.model.document import Document


class CNComplementarySubcategory(Document):
    def validate(self):
        previous = self.get_doc_before_save()
        if previous and previous.effect != self.effect and frappe.db.exists('CN Complementary Item', {'subcategory': self.name}):
            frappe.throw(_('No cambie el tratamiento de una subcategoría utilizada; cree otra para conservar la trazabilidad.'))

    def before_rename(self, old, new, merge=False):
        if merge:
            frappe.throw(_('No se pueden fusionar subcategorías: podrían tener tratamientos diferentes.'))
