import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from credinomina_reconciliation.parsers import clean_text


class CNDepositSurplus(Document):
    def validate(self):
        if not self.period and not self.registered_deposit:
            frappe.throw(_("Indique un período o seleccione un depósito registrado."))
        if self.period:
            self.employer = frappe.db.get_value(
                "CN Reconciliation Period", self.period, "employer"
            )
        if self.registered_deposit:
            deposit = frappe.db.get_value(
                "CN Remittance Allocation", self.registered_deposit,
                ["docstatus", "employer", "deposit_reference", "deposit_voucher", "deposit_date"],
                as_dict=True,
            )
            if not deposit or deposit.docstatus != 1 or not deposit.deposit_date:
                frappe.throw(_("Seleccione un depósito registrado y confirmado."))
            if self.employer and self.employer != deposit.employer:
                frappe.throw(_("El depósito y el período pertenecen a empresas diferentes."))
            self.employer = deposit.employer
            self.deposit_reference = deposit.deposit_reference
            self.deposit_voucher = deposit.deposit_voucher
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
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
        if self.period and frappe.db.get_value(
            "CN Reconciliation Period", self.period, "status"
        ) == "Cerrado":
            frappe.throw(_("No se puede cancelar el excedente de un periodo cerrado."))
        if self.registered_deposit:
            detail = frappe.db.get_value(
                "CN Remittance Allocation", self.registered_deposit, "allocation_detail"
            )
            for entry in json.loads(detail or "[]"):
                if entry.get("periodo") and frappe.db.get_value(
                    "CN Reconciliation Period", entry["periodo"], "status"
                ) == "Cerrado":
                    frappe.throw(_("El depósito participa en un período cerrado; no se puede cancelar el excedente."))

    def on_cancel(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
