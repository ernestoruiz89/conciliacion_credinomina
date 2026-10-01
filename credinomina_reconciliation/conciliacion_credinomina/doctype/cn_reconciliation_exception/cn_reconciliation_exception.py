import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import getdate, now_datetime


_ACTION_FIELDS = (
    "idx", "action_at", "action_by", "action_type", "details",
    "external_reference", "evidence_file",
)


def new_exception_name(period):
    prefix, digits = f"CN-EXC-{now_datetime().year}-", 4
    if period:
        payroll_month = frappe.db.get_value(
            "CN Reconciliation Period", period, "payroll_month"
        )
        if not payroll_month:
            frappe.throw(_("El período seleccionado no tiene mes de cobranza."))
        date = getdate(payroll_month)
        prefix, digits = f"CN-EXC-{date.month}-{date.year}-", 3
    while True:
        name = prefix + getseries(prefix, digits)
        if not frappe.db.exists("CN Reconciliation Exception", name):
            return name


class CNReconciliationException(Document):
    def autoname(self):
        self.name = new_exception_name(self.period)

    def validate(self):
        previous = self.get_doc_before_save()
        self._assert_related_periods_open(previous)
        from credinomina_reconciliation.exception_selection import validate_selected_case

        validate_selected_case(self, previous)
        if self.status in {"Resuelta", "Descartada"} and not self.resolution:
            frappe.throw(_("Escriba la resolucion antes de cerrar la excepcion."))
        # Existing review/resolution records are grandfathered until edited,
        # but a confirmed field may never be cleared after the transition.
        newly_entered_status = not previous or self.status != previous.status
        if self.status == "En revision" and any(
            not self.get(field) and (newly_entered_status or previous.get(field))
            for field in ("assigned_to", "next_action", "commitment_date")
        ):
            frappe.throw(_(
                "Para poner la excepcion en revision indique responsable, proxima gestion y fecha compromiso."
            ))
        if self.status == "Resuelta" and (
            (not self.assigned_to and (newly_entered_status or previous.assigned_to))
            or (
                self.cause_category in (None, "", "Por determinar")
                and (newly_entered_status or previous.cause_category not in (None, "", "Por determinar"))
            )
        ):
            frappe.throw(_(
                "Para resolver la excepcion indique responsable y causa confirmada."
            ))
        self._validate_follow_up_actions(previous)

    def _assert_related_periods_open(self, previous=None):
        periods = {self.period}
        if previous and previous.period:
            periods.add(previous.period)
        for period_name in periods - {None, ""}:
            if frappe.db.get_value(
                "CN Reconciliation Period", period_name, "status"
            ) == "Cerrado":
                frappe.throw(_(
                    "El período {0} está cerrado. Use Reabrir período antes de modificar "
                    "sus excepciones o gestiones."
                ).format(period_name))

    def on_trash(self):
        self._assert_related_periods_open()

    def before_cancel(self):
        self._assert_related_periods_open()

    def before_update_after_submit(self):
        self._assert_related_periods_open(self.get_doc_before_save())

    def after_delete(self):
        self._refresh_period_exception_count(self.period)

    @staticmethod
    def _refresh_period_exception_count(period_name):
        if not period_name or frappe.db.get_value(
            "CN Reconciliation Period", period_name, "reconciliation_mode"
        ) == "Historica":
            return
        count = frappe.db.count(
            "CN Reconciliation Exception",
            {
                "period": period_name,
                "status": ["in", ["Abierta", "En revision"]],
            },
        )
        frappe.db.set_value(
            "CN Reconciliation Period", period_name, "exception_count", count,
            update_modified=False,
        )

    def _validate_follow_up_actions(self, previous):
        old_actions = {
            action.name: action
            for action in (previous.follow_up_actions or [])
        } if previous else {}
        current_names = set()
        for action in self.follow_up_actions or []:
            if not (action.details or "").strip():
                frappe.throw(_("Escriba el detalle de cada gestion de la excepcion."))
            if action.name and action.name in current_names:
                frappe.throw(_("No repita una gestion en el historial."))
            if action.name:
                current_names.add(action.name)
            old = old_actions.get(action.name)
            if old:
                if any(
                    str(action.get(field) or "") != str(old.get(field) or "")
                    for field in _ACTION_FIELDS
                ):
                    frappe.throw(_(
                        "Las gestiones guardadas no se pueden modificar; agregue una nueva."
                    ))
            else:
                action.action_at = now_datetime().replace(microsecond=0)
                action.action_by = frappe.session.user
        if set(old_actions) - current_names:
            frappe.throw(_(
                "Las gestiones guardadas no se pueden eliminar; agregue una nueva."
            ))

    def on_update(self):
        previous = self.get_doc_before_save()
        self._assert_related_periods_open(previous)
        self._refresh_period_exception_count(self.period)
        if previous and previous.period and previous.period != self.period:
            self._refresh_period_exception_count(previous.period)
        if self.flags.get("skip_comment_reconciliation"):
            return
        if (
            (self.collection_row_id or (previous and previous.collection_row_id))
            and (
                not previous
                or any(
                    (self.get(field) or "") != (previous.get(field) or "")
                    for field in ("description", "resolution", "status", "collection_row_id", "period")
                )
            )
            and frappe.db.exists(
                "CN Accounting Import",
                {"status": ["in", ["Importado", "Importado con excepciones"]]},
            )
        ):
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
                reconcile_all_sources,
            )

            reconcile_all_sources()
