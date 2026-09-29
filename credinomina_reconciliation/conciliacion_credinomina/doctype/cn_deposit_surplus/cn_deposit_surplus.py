import json

import frappe
from frappe import _
from frappe.model.document import Document
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import money


class CNDepositSurplus(Document):
    def _ensure_related_periods_open(self):
        """Do not change a closed period through its deposit's surplus ledger."""
        periods = {self.period} if self.period else set()
        if self.registered_deposit:
            deposit = frappe.get_doc("CN Remittance Allocation", self.registered_deposit)
            if deposit.detail_period:
                periods.add(deposit.detail_period)
            for target in deposit.targets or []:
                if target.period:
                    periods.add(target.period)
                if target.historical_application:
                    historical_period = frappe.db.get_value(
                        "CN Source Row", target.historical_application, "historical_period"
                    )
                    if historical_period:
                        periods.add(historical_period)
                if target.complementary_item:
                    item_period = frappe.db.get_value(
                        "CN Complementary Item", target.complementary_item, "period"
                    )
                    if item_period:
                        periods.add(item_period)
            try:
                allocations = json.loads(deposit.allocation_detail or "[]")
            except (TypeError, ValueError):
                allocations = []
            for entry in allocations if isinstance(allocations, list) else []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("periodo"):
                    periods.add(entry["periodo"])
                if entry.get("partida"):
                    item_period = frappe.db.get_value(
                        "CN Complementary Item", entry["partida"], "period"
                    )
                    if item_period:
                        periods.add(item_period)
        closed = [name for name in periods if frappe.db.get_value(
            "CN Reconciliation Period", name, "status"
        ) == "Cerrado"]
        if closed:
            frappe.throw(_(
                "El excedente afecta un período cerrado ({0}). Reábralo antes de modificar el saldo a favor."
            ).format(", ".join(sorted(closed))))

    def validate(self):
        self.amount_usd = money(self.amount_usd)
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
        if money(self.amount_usd) <= 0:
            frappe.throw(_("El excedente debe ser mayor que cero."))
        if not clean_text(self.explanation):
            frappe.throw(_("Documente el motivo y tratamiento del excedente."))
        self._ensure_related_periods_open()

    def before_submit(self):
        self._ensure_related_periods_open()

    def on_submit(self):
        self._reconcile()
        result = frappe.db.get_value(self.doctype, self.name, "result")
        if result != "Saldo a favor documentado":
            frappe.throw(
                _("El excedente no se pudo confirmar: {0}.").format(result or "Pendiente")
            )

    def before_cancel(self):
        self._ensure_related_periods_open()

    def on_cancel(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
