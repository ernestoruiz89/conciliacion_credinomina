import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from credinomina_reconciliation.parsers import clean_text


class CNDepositSurplus(Document):
    def validate(self):
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        if self.period:
            self.employer = frappe.db.get_value(
                "CN Reconciliation Period", self.period, "employer"
            )
        if flt(self.amount_usd) <= 0:
            frappe.throw(_("El excedente debe ser mayor que cero."))
        if not clean_text(self.explanation):
            frappe.throw(_("Documente el motivo y tratamiento del excedente."))

    def on_submit(self):
        self._reconcile()
        result = frappe.db.get_value(self.doctype, self.name, "result")
        if result != "Saldo a favor documentado":
            frappe.throw(
                _("El excedente no se pudo confirmar: {0}.").format(result or "Pendiente")
            )

    def before_cancel(self):
        if frappe.db.get_value(
            "CN Reconciliation Period", self.period, "status"
        ) == "Cerrado":
            frappe.throw(_("No se puede cancelar el excedente de un periodo cerrado."))

    def on_cancel(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
