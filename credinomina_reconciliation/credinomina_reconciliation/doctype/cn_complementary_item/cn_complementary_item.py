import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CNComplementaryItem(Document):
    def validate(self):
        self.reference = (self.reference or "").strip()
        self.voucher = (self.voucher or "").strip()
        self.voucher_line = (self.voucher_line or "").strip()
        if flt(self.amount) <= 0:
            frappe.throw(_("El importe complementario debe ser mayor que cero."))
        if self.currency == "NIO":
            if flt(self.fx_rate) <= 0 or not (self.fx_evidence or "").strip():
                frappe.throw(
                    _("Para un asiento en C$ indique la tasa C$ por US$ y su fuente.")
                )
            self.amount_usd = round(flt(self.amount) / flt(self.fx_rate), 4)
        else:
            self.amount_usd = round(flt(self.amount), 4)
        duplicate = frappe.db.get_value(
            self.doctype,
            {
                "name": ["!=", self.name or ""],
                "docstatus": ["<", 2],
                "voucher": self.voucher,
                "voucher_line": self.voucher_line,
            },
            "name",
        )
        if duplicate:
            frappe.throw(
                _("El asiento y linea ya estan registrados en {0}.").format(
                    duplicate
                )
            )
        if self.period and self.employer:
            period_employer = frappe.db.get_value(
                "CN Reconciliation Period", self.period, "employer"
            )
            if period_employer != self.employer:
                frappe.throw(_("El periodo no pertenece a la empresa indicada."))

    def on_submit(self):
        self._reconcile()

    def on_cancel(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.credinomina_reconciliation.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
