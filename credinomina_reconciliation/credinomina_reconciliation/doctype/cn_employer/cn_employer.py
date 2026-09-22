import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CNEmployer(Document):
    def validate(self):
        tolerance = flt(self.rounding_tolerance_usd, 4)
        if tolerance < 0 or tolerance > 0.10:
            frappe.throw(_("La tolerancia automática debe estar entre US$ 0.00 y US$ 0.10."))

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous or flt(previous.rounding_tolerance_usd, 4) == flt(
            self.rounding_tolerance_usd, 4
        ):
            return
        if not frappe.db.exists(
            "CN Source Import",
            {"status": ["in", ["Importado", "Importado con excepciones"]]},
        ):
            return
        from credinomina_reconciliation.credinomina_reconciliation.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
