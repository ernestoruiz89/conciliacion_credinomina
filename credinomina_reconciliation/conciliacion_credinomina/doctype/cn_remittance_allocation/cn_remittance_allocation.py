import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from credinomina_reconciliation.parsers import clean_text


class CNRemittanceAllocation(Document):
    def validate(self):
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self.row_key = clean_text(self.row_key)
        self.historical_application = clean_text(self.historical_application)
        if flt(self.amount_usd) <= 0:
            frappe.throw(_("El importe a distribuir debe ser mayor que cero."))
        collection_target = bool(self.row_key)
        target_count = sum(
            bool(value)
            for value in (collection_target, self.historical_application, self.complementary_item)
        )
        if target_count != 1:
            frappe.throw(
                _("Elija una cobranza, una aplicación histórica o una partida complementaria.")
            )
        if collection_target:
            if not self.period or not self.row_key:
                frappe.throw(_("Indique tanto el periodo como la Fila ID de cobranza."))
            period = frappe.get_doc("CN Reconciliation Period", self.period)
            if period.reconciliation_mode == "Historica":
                frappe.throw(_("En un período histórico distribuya hacia la aplicación, no hacia una fila de cobranza."))
            matches = [row for row in period.collection_rows if row.row_key == self.row_key]
            if len(matches) != 1:
                frappe.throw(_("La Fila ID no identifica una cobranza unica en el periodo."))
        elif self.historical_application:
            if self.period or self.row_key:
                frappe.throw(_("No combine una aplicación histórica con un destino de cobranza."))
            application = frappe.db.get_value(
                "CN Source Row", self.historical_application,
                ["event_type", "historical_period", "currency", "effective"], as_dict=True,
            )
            if (
                not application or application.event_type != "Aplicacion"
                or not application.historical_period or application.currency != "USD"
                or not application.effective
            ):
                frappe.throw(_("El ID no corresponde a una aplicación histórica en US$."))
            if frappe.db.get_value(
                "CN Reconciliation Period", application.historical_period, "status"
            ) == "Cerrado":
                frappe.throw(_("El período histórico de la aplicación está cerrado."))
        else:
            if self.period or self.row_key:
                frappe.throw(_("No combine una partida complementaria con una fila de cobranza."))
            if frappe.db.get_value(
                "CN Complementary Item", self.complementary_item, "docstatus"
            ) != 1:
                frappe.throw(_("Confirme primero la partida complementaria."))

    def on_submit(self):
        self._reconcile()
        result = frappe.db.get_value(self.doctype, self.name, "result")
        if result != "Aplicada":
            frappe.throw(
                _("La distribucion no se pudo confirmar: {0}.").format(result or "Pendiente")
            )

    def before_cancel(self):
        if self.period and frappe.db.get_value(
            "CN Reconciliation Period", self.period, "status"
        ) == "Cerrado":
            frappe.throw(_("No se puede cancelar una distribucion de un periodo cerrado."))
        if self.historical_application:
            period = frappe.db.get_value(
                "CN Source Row", self.historical_application, "historical_period"
            )
            if period and frappe.db.get_value(
                "CN Reconciliation Period", period, "status"
            ) == "Cerrado":
                frappe.throw(_("No se puede cancelar una distribución histórica cerrada."))

    def on_cancel(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
