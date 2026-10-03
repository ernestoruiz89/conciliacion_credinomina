import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from credinomina_reconciliation.rounding import decimal_value, money, money_float
from credinomina_reconciliation import complementary_compensation as compensation
from credinomina_reconciliation.application_adjustments import CATEGORY as APPLICATION_ADJUSTMENT, validate_adjustment, assert_adjustable
from credinomina_reconciliation.company_credit import CATEGORY, ensure_related_periods_open, validate_company_credit
from credinomina_reconciliation import client_credit
from credinomina_reconciliation.tolerance_items import (
    guard_tolerance_item, is_tolerance_item, validate_tolerance_item,
)


class CNComplementaryItem(Document):
    def validate(self):
        previous = self.get_doc_before_save() if hasattr(self, "get_doc_before_save") else None
        client_credit.guard_credit_category(self, previous)
        from credinomina_reconciliation.complementary_exceptions import guard_item_link, apply_registration_status
        guard_item_link(self, previous)
        compensation.guard_document(self, previous)
        if guard_tolerance_item(self, previous):
            validate_tolerance_item(self)
            apply_registration_status(self)
            return
        from credinomina_reconciliation.accounting_review import validate_review_item

        validate_review_item(self, previous)
        from credinomina_reconciliation.complementary_distribution import validate_distribution
        validate_distribution(self)
        self.amount = money(self.amount)
        self.reference = (self.reference or "").strip()
        if not self.reference and not self.get("accounting_source_key") and self.category not in {APPLICATION_ADJUSTMENT, compensation.CATEGORY, CATEGORY, client_credit.CATEGORY}:
            frappe.throw(_("Indique la referencia del depósito."))
        self.voucher = (self.voucher or "").strip()
        self.voucher_line = (self.voucher_line or "").strip()
        self.accounting_status = "Registrada" if self.voucher else "Pendiente de registro"
        apply_registration_status(self)
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
        compensation.update_totals(self)
        if self.category == APPLICATION_ADJUSTMENT:
            validate_adjustment(self)
        if self.category == CATEGORY:
            validate_company_credit(self)
        elif self.category == client_credit.CATEGORY:
            client_credit.validate_client_credit(self, previous)
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
        if self.category == client_credit.CATEGORY:
            from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
            reconcile_deposit(frappe.get_doc("CN Remittance Allocation", self.registered_deposit))
            self.reload()
            if self.result != client_credit.RESULT:
                frappe.throw(_("El saldo a favor del cliente no se pudo documentar: {0}.").format(self.result or "Pendiente"))
            return
        if self.category == compensation.CATEGORY:
            return  # Direct offsets never participate in deposit reconciliation.
        if self.category == APPLICATION_ADJUSTMENT:
            self._reconcile_application()
            return
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
        client_credit.guard_cancel(self)
        from credinomina_reconciliation.complementary_exceptions import guard_item_delete
        guard_item_delete(self)
        compensation.guard_delete(self)
        from credinomina_reconciliation.complementary_distribution import guard_closed_distributions
        guard_closed_distributions(self)
        guard_tolerance_item(self)
        if not is_tolerance_item(self) and self.category != compensation.CATEGORY:
            from credinomina_reconciliation.complementary_cancellation import prepare_cancellation
            self.flags.cancellation_scope = prepare_cancellation(self)
            # Keep the canceled target as audit evidence. Only this dependency
            # is allowed: its confirmed deposit is recalculated in on_cancel.
            self.ignore_linked_doctypes = ["CN Remittance Target"]
        if self.category == APPLICATION_ADJUSTMENT:
            frappe.db.sql("select name from `tabCN Source Row` where name=%s for update", self.related_application)
            row = frappe.get_doc("CN Source Row", self.related_application)
            assert_adjustable(row, self.name,
                              frappe.parse_json(self.get("adjustment_periods") or "[]"),
                              frappe.parse_json(self.get("adjustment_collection_rows") or "[]"))
            from credinomina_reconciliation.application_adjustments import cash_coverage
            self.flags.adjustment_cash_snapshot = cash_coverage(row,
                frappe.parse_json(self.get("adjustment_collection_rows") or "[]"), lock=True)["snapshots"]
        if self.category in {CATEGORY, client_credit.CATEGORY}:
            ensure_related_periods_open(self)

    def on_trash(self):
        client_credit.guard_cancel(self)
        from credinomina_reconciliation.complementary_exceptions import guard_item_delete
        guard_item_delete(self)
        compensation.guard_delete(self)
        guard_tolerance_item(self)
        if self.category in {CATEGORY, client_credit.CATEGORY}:
            ensure_related_periods_open(self)

    def before_update_after_submit(self):
        self.validate()

    def on_cancel(self):
        if self.category == APPLICATION_ADJUSTMENT:
            self.db_set("review_status", "Ajuste cancelado", update_modified=False)
        if is_tolerance_item(self) or self.category == compensation.CATEGORY:
            return
        from credinomina_reconciliation.complementary_cancellation import reconcile_cancellation
        self.flags.cancellation_result = reconcile_cancellation(self, self.flags.cancellation_scope)

    def before_rename(self, old, new, merge=False):
        if self.category == client_credit.CATEGORY and self.docstatus == 1:
            frappe.throw(_("No se puede renombrar o fusionar un saldo a favor del cliente confirmado; conserve su seguimiento."))
        from credinomina_reconciliation.complementary_exceptions import guard_item_delete
        guard_item_delete(self)
        compensation.guard_delete(self)
        guard_tolerance_item(self)

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()

    def _reconcile_application(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
        from credinomina_reconciliation.application_adjustments import assert_cash_preserved

        _reconcile_sources(self.employer)
        assert_cash_preserved(self.flags.get("adjustment_cash_snapshot") or {})
