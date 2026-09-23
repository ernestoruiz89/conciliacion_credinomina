import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CNEmployer(Document):
    def validate(self):
        self.employer_name = (self.employer_name or "").strip()
        if not self.employer_name:
            frappe.throw(_("Indique el nombre de la empresa."))
        previous = self.get_doc_before_save()
        if (
            previous and previous.name == previous.employer_name
            and self.name != self.employer_name
        ):
            frappe.throw(_("Para cambiar el nombre de la empresa, use Renombrar."))
        tolerance = flt(self.rounding_tolerance_usd, 4)
        if tolerance < 0 or tolerance > 0.10:
            frappe.throw(_("La tolerancia automática debe estar entre US$ 0.00 y US$ 0.10."))

    def before_rename(self, old, new, merge=False):
        if merge:
            frappe.throw(_("No se pueden fusionar empresas Credinómina."))
        return (new or "").strip()

    def after_rename(self, old, new, merge=False):
        frappe.db.set_value(self.doctype, new, "employer_name", new, update_modified=False)
        self.employer_name = new

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous or (
            flt(previous.rounding_tolerance_usd, 4) == flt(self.rounding_tolerance_usd, 4)
            and previous.employer_code == self.employer_code
        ):
            return
        if not frappe.db.exists(
            "CN Source Import",
            {"status": ["in", ["Importado", "Importado con excepciones"]]},
        ):
            return
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
