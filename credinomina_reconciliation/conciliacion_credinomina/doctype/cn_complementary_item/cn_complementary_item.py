import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from credinomina_reconciliation.rounding import decimal_value, money, money_float
from credinomina_reconciliation.company_credit import CATEGORY, ensure_related_periods_open, validate_company_credit
from credinomina_reconciliation.tolerance_items import (
    guard_tolerance_item, is_tolerance_item, validate_tolerance_item,
)


class CNComplementaryItem(Document):
    def validate(self):
        previous = self.get_doc_before_save() if hasattr(self, "get_doc_before_save") else None
        if guard_tolerance_item(self, previous):
            validate_tolerance_item(self)
            return
        from credinomina_reconciliation.accounting_review import validate_review_item

        validate_review_item(self, previous)
        self.amount = money(self.amount)
        self.reference = (self.reference or "").strip()
        if not self.reference and not self.get("accounting_source_key"):
            frappe.throw(_("Indique la referencia del depósito."))
        self.voucher = (self.voucher or "").strip()
        self.voucher_line = (self.voucher_line or "").strip()
        self.accounting_status = "Registrada" if self.voucher else "Pendiente de registro"
        if not money(self.amount):
            frappe.throw(_("El importe complementario debe ser distinto de cero."))
        if self.currency == "NIO":
            if flt(self.fx_rate) <= 0:
                frappe.throw(
                    _("Para una partida en C$ indique una tasa C$ por US$ mayor que cero.")
                )
            self.amount_usd = money_float(decimal_value(self.amount) / decimal_value(self.fx_rate))
        elif self.currency == "USD":
            self.amount_usd = money_float(self.amount)
        else:
            frappe.throw(_("Seleccione USD o NIO como moneda de la partida."))
        if not money(self.amount_usd):
            frappe.throw(_("El equivalente en US$ debe ser distinto de cero."))
        if self.category == CATEGORY:
            validate_company_credit(self)
        duplicate = frappe.db.get_value(
            self.doctype,
            {
                "name": ["!=", self.name or ""],
                "docstatus": ["<", 2],
                "voucher": self.voucher,
                "voucher_line": self.voucher_line,
            },
            "name",
        ) if self.voucher else None
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
        if is_tolerance_item(self):
            return
        if self.category == CATEGORY or not self.flags.get("defer_reconciliation"):
            self._reconcile()
        if self.category == CATEGORY:
            result = frappe.db.get_value(self.doctype, self.name, "result")
            if result != "Saldo a favor documentado":
                frappe.throw(_("El saldo a favor no se pudo confirmar: {0}.").format(result or "Pendiente"))
            self.result = result

    def before_cancel(self):
        guard_tolerance_item(self)
        if self.category == CATEGORY:
            ensure_related_periods_open(self)

    def on_trash(self):
        guard_tolerance_item(self)
        if self.category == CATEGORY:
            ensure_related_periods_open(self)

    def before_update_after_submit(self):
        self.validate()

    def on_cancel(self):
        if is_tolerance_item(self):
            return
        self._reconcile()

    def before_rename(self, old, new, merge=False):
        guard_tolerance_item(self)

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
