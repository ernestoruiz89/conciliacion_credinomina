import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from credinomina_reconciliation.parsers import (
    SourceFileError, clean_text, file_sha256, parse_collection_file,
)
from credinomina_reconciliation.client_registry import load_client_index
from credinomina_reconciliation.client_identity import choose_client
from credinomina_reconciliation.reconciliation import remittance_fx_basis


class CNRemittanceAllocation(Document):
    def validate(self):
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self._validate_deposit()

    def _validate_deposit(self):
        if not self.deposit_date:
            frappe.throw(_("Indique la fecha real del depósito."))
        if self.docstatus == 1:
            self._assert_open_related_periods()
        if not self.employer:
            frappe.throw(_("Indique la empresa del depósito."))
        if self.detail_period:
            period = frappe.db.get_value(
                "CN Reconciliation Period", self.detail_period,
                ["employer", "status"], as_dict=True,
            )
            if not period or period.employer != self.employer:
                frappe.throw(_("El período del detalle debe pertenecer a la empresa del depósito."))
            if period.status == "Cerrado":
                frappe.throw(_("No se puede asignar un detalle a un período cerrado."))
        if flt(self.deposit_amount) <= 0:
            frappe.throw(_("El importe del depósito debe ser mayor que cero."))
        if self.deposit_currency == "NIO":
            if flt(self.fx_rate) <= 0 or not remittance_fx_basis(self):
                frappe.throw(_(
                    "Para un depósito en C$ indique la tasa C$/US$ y documente "
                    "su fuente en Justificación o en el soporte adjunto."
                ))
            equivalent = round(flt(self.deposit_amount) / flt(self.fx_rate), 4)
        elif self.deposit_currency == "USD":
            equivalent = round(flt(self.deposit_amount), 4)
        else:
            frappe.throw(_("La moneda del depósito debe ser USD o NIO."))
        self.amount_usd = equivalent
        duplicates = frappe.get_all(
            "CN Remittance Allocation",
            filters={
                "docstatus": 1,
                "employer": self.employer,
                "deposit_reference": self.deposit_reference,
                "deposit_date": self.deposit_date,
                "deposit_currency": self.deposit_currency,
                "deposit_amount": self.deposit_amount,
            },
            fields=["name", "deposit_voucher"],
        )
        if any(
            row.name != self.name
            and clean_text(row.deposit_voucher) == self.deposit_voucher
            for row in duplicates
        ):
            frappe.throw(_("Este depósito ya fue registrado. Si son dos depósitos distintos, indique comprobantes diferentes."))
        assigned = 0
        for target in self.targets or []:
            self._validate_target(target)
            target_period = target.period
            if target.historical_application:
                target_period = frappe.db.get_value(
                    "CN Source Row", target.historical_application, "historical_period"
                )
            if target.complementary_item:
                target_period = frappe.db.get_value(
                    "CN Complementary Item", target.complementary_item, "period"
                )
            target_employer = (
                frappe.db.get_value("CN Reconciliation Period", target_period, "employer")
                if target_period else None
            )
            if target.complementary_item:
                target_employer = frappe.db.get_value(
                    "CN Complementary Item", target.complementary_item, "employer"
                ) or target_employer
            if target_employer and target_employer != self.employer:
                frappe.throw(_("Un destino pertenece a una empresa diferente del depósito."))
            assigned += flt(target.amount_usd)
        if assigned > equivalent + 0.00005:
            frappe.throw(_("Los destinos superan el importe del depósito en US$."))

    @staticmethod
    def _validate_target(target):
        target.row_key = clean_text(target.row_key)
        target.historical_application = clean_text(target.historical_application)
        if flt(target.amount_usd) <= 0:
            frappe.throw(_("El importe a distribuir debe ser mayor que cero."))
        collection_target = bool(target.row_key)
        target_count = sum(
            bool(value)
            for value in (collection_target, target.historical_application, target.complementary_item)
        )
        if target_count != 1:
            frappe.throw(
                _("Elija una cobranza, una aplicación histórica o una partida complementaria.")
            )
        if collection_target:
            if not target.period or not target.row_key:
                frappe.throw(_("Indique tanto el periodo como la Fila ID de cobranza."))
            period = frappe.get_doc("CN Reconciliation Period", target.period)
            if period.reconciliation_mode == "Historica":
                frappe.throw(_("En un período histórico distribuya hacia la aplicación, no hacia una fila de cobranza."))
            if period.status == "Cerrado":
                frappe.throw(_("El período de cobranza está cerrado."))
            matches = [row for row in period.collection_rows if row.row_key == target.row_key]
            if len(matches) != 1:
                frappe.throw(_("La Fila ID no identifica una cobranza unica en el periodo."))
        elif target.historical_application:
            if target.period or target.row_key:
                frappe.throw(_("No combine una aplicación histórica con un destino de cobranza."))
            application = frappe.db.get_value(
                "CN Source Row", target.historical_application,
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
            if target.period or target.row_key:
                frappe.throw(_("No combine una partida complementaria con una fila de cobranza."))
            if frappe.db.get_value(
                "CN Complementary Item", target.complementary_item, "docstatus"
            ) != 1:
                frappe.throw(_("Confirme primero la partida complementaria."))

    def on_submit(self):
        self._reconcile()

    def before_cancel(self):
        self._assert_open_related_periods()
        for target in self.targets or []:
            self._check_open_target(target)

    @staticmethod
    def _check_open_target(target):
        if target.period and frappe.db.get_value(
            "CN Reconciliation Period", target.period, "status"
        ) == "Cerrado":
            frappe.throw(_("No se puede cancelar un depósito de un periodo cerrado."))
        if target.historical_application:
            period = frappe.db.get_value(
                "CN Source Row", target.historical_application, "historical_period"
            )
            if period and frappe.db.get_value(
                "CN Reconciliation Period", period, "status"
            ) == "Cerrado":
                frappe.throw(_("No se puede cancelar una distribución histórica cerrada."))

    def _assert_open_related_periods(self):
        persisted = frappe.db.get_value(self.doctype, self.name, "allocation_detail")
        for entry in json.loads(persisted or "[]"):
            period = entry.get("periodo")
            if not period and entry.get("partida"):
                period = frappe.db.get_value(
                    "CN Complementary Item", entry["partida"], "period"
                )
            if period and frappe.db.get_value(
                "CN Reconciliation Period", period, "status"
            ) == "Cerrado":
                frappe.throw(_("El depósito participa en un período cerrado; no se puede modificar ni cancelar."))

    def on_cancel(self):
        self._reconcile()

    def before_update_after_submit(self):
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self._validate_deposit()

    def on_update_after_submit(self):
        self._reconcile()

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()


@frappe.whitelist(methods=["POST"])
def import_remittance_detail(remittance_name: str):
    """Store the company's per-client deductions; reconciliation remains auditable."""
    document = frappe.get_doc("CN Remittance Allocation", remittance_name)
    document.check_permission("write")
    if not document.deposit_date or document.docstatus == 2:
        frappe.throw(_("Registre primero un depósito válido."))
    source_url = document.detail_file or document.support_file
    if not source_url:
        frappe.throw(_("Adjunte el archivo de detalle por cliente."))
    files = frappe.get_all(
        "File",
        filters={
            "file_url": source_url,
            "attached_to_doctype": document.doctype,
            "attached_to_name": document.name,
        },
        pluck="name", limit_page_length=1,
    )
    if not files:
        frappe.throw(_("El detalle debe estar adjunto a este depósito."))
    file_doc = frappe.get_doc("File", files[0])
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    try:
        records = parse_collection_file(
            file_doc.file_name, content,
            require_deduction=True, keep_zero_rows=True, require_name=True,
        )
    except SourceFileError as exc:
        frappe.throw(str(exc), title=_("Detalle de depósito inválido"))
    document.set("detail_rows", [])
    clients = load_client_index()
    for record in records:
        client, identity_reason = choose_client(record, clients, document.employer)
        document.append("detail_rows", {
            key: record.get(key) for key in (
                "source_row", "row_key", "client_number", "employee_number", "client_name",
                "national_id", "loan_number", "installment_number",
                "application_reference", "comments", "application_comment",
                "expected_usd", "expected_nio",
                "deducted_usd", "deducted_nio",
            )
        } | {
            "client": client["name"] if client else "",
            "identity_reason": identity_reason,
        })
    document.detail_hash = file_sha256(content)
    document.detail_source_file = source_url
    document.detail_imported_on = now_datetime()
    document.detail_count = len(records)
    document.detail_status = "Cargado; pendiente de conciliación"
    document.save()
    return {
        "deposit": document.name,
        "rows": len(records),
        "detail_status": frappe.db.get_value(
            "CN Remittance Allocation", document.name, "detail_status"
        ),
    }
