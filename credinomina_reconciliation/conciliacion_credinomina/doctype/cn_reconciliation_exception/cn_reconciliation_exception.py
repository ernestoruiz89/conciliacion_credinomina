import frappe
from frappe import _
from frappe.model.document import Document


class CNReconciliationException(Document):
    def validate(self):
        if self.status in {"Resuelta", "Descartada"} and not self.resolution:
            frappe.throw(_("Escriba la resolucion antes de cerrar la excepcion."))

    def on_update(self):
        if not self.period:
            return
        count = frappe.db.count(
            self.doctype,
            {
                "period": self.period,
                "status": ["in", ["Abierta", "En revision"]],
            },
        )
        if frappe.db.get_value(
            "CN Reconciliation Period", self.period, "reconciliation_mode"
        ) != "Historica":
            frappe.db.set_value(
                "CN Reconciliation Period",
                self.period,
                "exception_count",
                count,
                update_modified=False,
            )
        if self.flags.get("skip_comment_reconciliation"):
            return
        previous = self.get_doc_before_save()
        if (
            self.collection_row_id
            and (
                not previous
                or any(
                    (self.get(field) or "") != (previous.get(field) or "")
                    for field in ("description", "resolution", "status")
                )
            )
            and frappe.db.exists(
                "CN Source Import",
                {"status": ["in", ["Importado", "Importado con excepciones"]]},
            )
        ):
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
                reconcile_all_sources,
            )

            reconcile_all_sources()
