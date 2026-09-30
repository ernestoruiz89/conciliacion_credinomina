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
from credinomina_reconciliation.rounding import (
    MONEY_EPSILON, decimal_value, money, money_float,
)


@frappe.whitelist()
def get_pending_targets(remittance_name, targets=None):
    from credinomina_reconciliation.remittance_selection import get_pending_targets as load
    return load(remittance_name, targets)


@frappe.whitelist()
def get_detail_credits(remittance_name, detail_row_name):
    from credinomina_reconciliation.remittance_credit_selection import get_detail_credits as load
    return load(remittance_name, detail_row_name)


@frappe.whitelist(methods=["POST"])
def set_detail_credit(remittance_name, detail_row_name, portfolio_row_name, modified):
    from credinomina_reconciliation.remittance_credit_selection import set_detail_credit as assign
    return assign(remittance_name, detail_row_name, portfolio_row_name, modified)


class CNRemittanceAllocation(Document):
    def validate(self):
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self._validate_deposit()
        self._invalidate_changed_detail_credits()

    def _invalidate_changed_detail_credits(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        old_rows = {row.get("name"): row for row in previous.get("detail_rows") or []}
        for row in self.get("detail_rows") or []:
            old = old_rows.get(row.get("name"))
            row.loan_number = clean_text(row.get("loan_number"))
            if not old or row.loan_number == clean_text(old.get("loan_number")):
                continue
            if self.flags.get("portfolio_selected_detail") != row.name:
                row.loan_selection_snapshot = None
                row.loan_selection_note = _("{0} — {1}: {2} → {3}. Edición manual.").format(
                    now_datetime(), frappe.session.user,
                    old.get("loan_number") or _("Sin crédito"), row.loan_number or _("Sin crédito"),
                )
            row.match_status = "Pendiente"
            row.match_reason = _("Crédito actualizado; pendiente de conciliación.")
            row.matched_targets = "[]"
            row.matched_targets_summary = _("Use Conciliar para actualizar los destinos. Revise los destinos manuales si cambió el crédito.")
            self.detail_status = "Cargado; pendiente de conciliación"
            self.result = "Pendiente"

    def _validate_deposit(self):
        self.deposit_amount = money(self.deposit_amount)
        if not self.deposit_date:
            frappe.throw(_("Indique la fecha real del depósito."))
        if self.docstatus == 1:
            self._assert_open_related_periods()
        if not self.employer:
            frappe.throw(_("Indique la empresa del depósito."))
        if self.get("detail_rows"):
            from credinomina_reconciliation.remittance_credit_selection import complete_detail_clients
            complete_detail_clients(self.detail_rows, load_client_index(), self.employer)
        if self.detail_period:
            period = frappe.db.get_value(
                "CN Reconciliation Period", self.detail_period,
                ["employer", "status"], as_dict=True,
            )
            if not period or period.employer != self.employer:
                frappe.throw(_("El período del detalle debe pertenecer a la empresa del depósito."))
            if period.status == "Cerrado":
                frappe.throw(_("No se puede asignar un detalle a un período cerrado."))
        if money(self.deposit_amount) <= 0:
            frappe.throw(_("El importe del depósito debe ser mayor que cero."))
        if self.deposit_currency == "NIO":
            if flt(self.fx_rate) <= 0:
                frappe.throw(_("Para un depósito en C$ indique la tasa C$/US$."))
            equivalent = money_float(decimal_value(self.deposit_amount) / decimal_value(self.fx_rate))
        elif self.deposit_currency == "USD":
            equivalent = money_float(self.deposit_amount)
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
        assigned = decimal_value(0)
        for target in self.targets or []:
            self._validate_target(target)
            detail_row = next((row for row in self.detail_rows or []
                               if row.name == target.get("detail_row")), None)
            if target.get("detail_row") and not detail_row:
                frappe.throw(_("La fila de detalle vinculada ya no existe en este depósito. Quite el vínculo y vuelva a seleccionar la fila."))
            target.detail_row_label = (
                _("Fila {0} · {1}").format(detail_row.source_row or detail_row.idx, detail_row.client_name)
                if detail_row else ""
            )
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
            assigned += money(target.amount_usd)
        if assigned > money(equivalent) + MONEY_EPSILON:
            frappe.throw(_("Los destinos superan el importe del depósito en US$."))

    @staticmethod
    def _validate_target(target):
        target.amount_usd = money(target.amount_usd)
        target.row_key = clean_text(target.row_key)
        target.historical_application = clean_text(target.historical_application)
        if not money(target.amount_usd) or (money(target.amount_usd) < 0 and not target.complementary_item):
            frappe.throw(_("Solo una partida complementaria puede tener importe negativo; el importe no puede ser cero."))
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
            complementary = frappe.db.get_value(
                "CN Complementary Item", target.complementary_item,
                ["docstatus", "period", "amount_usd", "category"], as_dict=True,
            )
            if not complementary or complementary.docstatus != 1:
                frappe.throw(_("Confirme primero la partida complementaria."))
            if complementary.category == "Saldo a favor de la empresa":
                frappe.throw(_("El saldo a favor se vincula desde la partida al depósito; no se asigna como pago en Destinos."))
            if money(target.amount_usd) * money(complementary.amount_usd) <= 0 or abs(money(target.amount_usd)) > abs(money(complementary.amount_usd)):
                frappe.throw(_("El destino debe tener el signo de la partida complementaria y no superar su importe."))
            if complementary.period and frappe.db.get_value(
                "CN Reconciliation Period", complementary.period, "status"
            ) == "Cerrado":
                frappe.throw(_("El período de la partida complementaria está cerrado."))

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
        self._invalidate_changed_detail_credits()
        previous = self.get_doc_before_save()
        if previous and self._reconciliation_inputs_changed(previous):
            self.result = "Pendiente"

    def _reconciliation_inputs_changed(self, previous):
        fields = (
            "employer", "deposit_reference", "deposit_voucher", "deposit_date",
            "deposit_currency", "deposit_amount", "fx_rate", "detail_period",
            "detail_file", "detail_hash",
        )
        if any(
            str(self.get(fieldname) or "") != str(previous.get(fieldname) or "")
            for fieldname in fields
        ):
            return True
        target_fields = (
            "period", "row_key", "historical_application", "complementary_item",
            "amount_usd", "detail_row",
        )
        current_targets = [
            tuple(str(target.get(fieldname) or "") for fieldname in target_fields)
            for target in self.targets or []
        ]
        previous_targets = [
            tuple(str(target.get(fieldname) or "") for fieldname in target_fields)
            for target in previous.targets or []
        ]
        if current_targets != previous_targets:
            return True
        detail_fields = ("name", "client", "client_number", "loan_number")
        def detail_identity(document):
            return [tuple(row.get(field) or "" for field in detail_fields)
                    for row in (document.get("detail_rows") or [])]
        return detail_identity(self) != detail_identity(previous)

    def _reconcile(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
            reconcile_all_sources,
        )

        return reconcile_all_sources()


@frappe.whitelist(methods=["POST"])
def create_complementary_item(remittance_name: str, modified: str, values):
    """Create, confirm and attach a complement in one permission-checked transaction."""
    frappe.db.sql("select name from `tabCN Remittance Allocation` where name=%s for update", (remittance_name,))
    document = frappe.get_doc("CN Remittance Allocation", remittance_name)
    document.check_permission("write")
    if document.docstatus == 2:
        frappe.throw(_("El depósito está cancelado."))
    document._assert_open_related_periods()
    if str(document.modified) != str(modified):
        frappe.throw(_("El depósito cambió. Recárguelo antes de crear la partida."))
    if not frappe.has_permission("CN Complementary Item", "create") or not frappe.has_permission("CN Complementary Item", "submit"):
        frappe.throw(_("Necesita permisos para crear y confirmar partidas complementarias."), frappe.PermissionError)
    values = frappe.parse_json(values) if isinstance(values, str) else values
    if not isinstance(values, dict):
        frappe.throw(_("Los datos de la partida no son válidos."))
    item = frappe.new_doc("CN Complementary Item")
    for field in ("category", "voucher", "voucher_line", "posting_date", "currency", "amount",
                  "fx_rate", "period", "client_number", "loan_number", "installment_number", "description", "reason_type"):
        if field in values:
            item.set(field, values[field])
    item.reference = document.deposit_reference
    item.employer = document.employer
    company_credit = item.category == "Saldo a favor de la empresa"
    if company_credit:
        item.registered_deposit = document.name
    if item.period and frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
        frappe.throw(_("El período está cerrado."))
    item.flags.defer_reconciliation = True
    item.insert()
    item.submit()
    if not company_credit:
        document.append("targets", {
            "complementary_item": item.name, "amount_usd": item.amount_usd,
            "notes": item.description,
        })
        document.save()
    return {"name": item.name, "accounting_status": item.accounting_status,
            "company_credit": company_credit, "result": item.result}


@frappe.whitelist(methods=["POST"])
def reconcile_remittance(remittance_name: str):
    """Run reconciliation only when the user explicitly requests it."""
    document = frappe.get_doc("CN Remittance Allocation", remittance_name)
    document.check_permission("write")
    if document.docstatus != 1:
        frappe.throw(_("Confirme el depósito antes de conciliarlo."))
    document._validate_deposit()
    return document._reconcile()


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
    _apply_remittance_detail(document, records, content, source_url)
    return {
        "deposit": document.name,
        "rows": len(records),
        "detail_status": document.detail_status,
    }


def _apply_remittance_detail(document, records, content, source_url, origin="Archivo importado"):
    document.set("detail_rows", [])
    # Reimport creates new row identities; prior manual evidence must be reviewed.
    for target in document.targets or []:
        target.detail_row = ""
        target.detail_row_label = ""
    clients = load_client_index()
    for record in records:
        client, identity_reason = choose_client(record, clients, document.employer)
        document.append("detail_rows", {
            key: record.get(key) for key in (
                "source_row", "row_key", "client_number", "employee_number", "client_name",
                "national_id", "loan_number", "installment_number",
                "application_reference", "comments", "application_comment",
                "deducted_usd", "deducted_nio",
            )
        } | {
            "client": client["name"] if client else "",
            "identity_reason": identity_reason,
            "client_number": record.get("client_number") or (client.get("client_number") if client else ""),
        })
    document.detail_hash = file_sha256(content)
    document.detail_source_file = source_url
    document.detail_origin = origin
    document.detail_imported_on = now_datetime()
    document.detail_count = len(records)
    document.detail_status = "Cargado; pendiente de conciliación"
    document.save()


@frappe.whitelist()
def preview_application_detail(remittance_name: str):
    from credinomina_reconciliation.application_deposit_detail import preview_application_detail as preview
    return preview(remittance_name)


@frappe.whitelist(methods=["POST"])
def use_application_detail(remittance_name: str, fingerprint: str, replace_detail=False, selected_claim_ids=None):
    from credinomina_reconciliation.application_deposit_detail import use_application_detail as apply
    return apply(remittance_name, fingerprint, replace_detail, selected_claim_ids)
