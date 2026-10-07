import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from credinomina_reconciliation.parsers import (
    SourceFileError, clean_text, file_sha256, normalize_credit_number, parse_collection_file,
)
from credinomina_reconciliation.client_registry import load_client_index
from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.paying_employers import allowed_employers, choose_detail_client
from credinomina_reconciliation.reconciliation import remittance_fx_basis
from credinomina_reconciliation.tolerance_items import CATEGORY as TOLERANCE_CATEGORY
from credinomina_reconciliation.remittance_periods import selected_periods
from credinomina_reconciliation.rounding import (
    MONEY_EPSILON, decimal_value, money, money_float,
)


@frappe.whitelist()
def get_pending_targets(remittance_name, targets=None, detail_row_name=None):
    from credinomina_reconciliation.remittance_selection import get_pending_targets as load
    return load(remittance_name, targets, detail_row_name=detail_row_name)


@frappe.whitelist()
def get_detail_credits(remittance_name, detail_row_name):
    from credinomina_reconciliation.remittance_credit_selection import get_detail_credits as load
    return load(remittance_name, detail_row_name)


@frappe.whitelist(methods=["POST"])
def set_detail_credit(remittance_name, detail_row_name, portfolio_row_name, modified):
    from credinomina_reconciliation.remittance_credit_selection import set_detail_credit as assign
    return assign(remittance_name, detail_row_name, portfolio_row_name, modified)


class CNRemittanceAllocation(Document):
    def autoname(self):
        from credinomina_reconciliation.deposit_naming import new_deposit_name
        self.name = new_deposit_name(self.deposit_date)

    def validate(self):
        previous = self.get_doc_before_save()
        from credinomina_reconciliation.accounting_deposits import validate_evidence
        validate_evidence(self, previous)
        if previous and self.get("reconciliation_identity") != previous.get("reconciliation_identity"):
            frappe.throw(_("No se puede modificar la identidad interna del depósito."))
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self._validate_deposit()
        self._invalidate_changed_detail_credits()
        from credinomina_reconciliation.detail_balances import update_detail_balances
        update_detail_balances(self)

    def _invalidate_changed_detail_credits(self):
        previous = self.get_doc_before_save()
        if not previous:
            return
        old_rows = {row.get("name"): row for row in previous.get("detail_rows") or []}
        for row in self.get("detail_rows") or []:
            old = old_rows.get(row.get("name"))
            row.loan_number = clean_text(row.get("loan_number"))
            if not old or (row.loan_number == clean_text(old.get("loan_number"))
                           and row.get("employer") == old.get("employer")):
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
        allowed = allowed_employers(self.employer)
        self.deposit_amount = money(self.deposit_amount)
        if not self.deposit_date:
            frappe.throw(_("Indique la fecha real del depósito."))
        if self.docstatus == 1:
            self._assert_open_related_periods()
        if not self.employer and (self.docstatus != 0 or not self.get("accounting_source_key")):
            frappe.throw(_("Indique la empresa del depósito."))
        if self.get("detail_rows"):
            from credinomina_reconciliation.remittance_credit_selection import complete_detail_clients
            for row in self.detail_rows:
                if not any(clean_text(row.get(field)) for field in (
                    "loan_number", "national_id", "client_number", "employee_number", "client_name",
                )):
                    frappe.throw(_("Fila {0}: indique al menos un identificador del cliente.").format(row.idx))
                if row.get("employer") and row.employer not in allowed:
                    frappe.throw(_("La empresa del detalle no está autorizada por la empresa pagadora."))
            clients = load_client_index(employers=allowed)
            loans = load_detail_loan_clients(self.detail_rows, clients, allowed)
            complete_detail_clients(self.detail_rows, clients, self.employer, allowed, loans)
        selected = [row.period for row in self.get("detail_periods") or []]
        if self.get("apply_fifo") and not any(selected):
            frappe.throw(_("Seleccione al menos un período en Períodos a conciliar para aplicar FIFO."))
        if len(selected) != len(set(selected)):
            frappe.throw(_("No repita períodos en Períodos del detalle."))
        self.applied_usd = money(0)
        for row in self.get("detail_periods") or []:
            period = frappe.db.get_value(
                "CN Reconciliation Period", row.period,
                ["employer", "status", "applied_usd"], as_dict=True,
            )
            if not period or period.employer not in allowed:
                frappe.throw(_("El período del detalle debe pertenecer a la pagadora o a una empresa autorizada."))
            if period.status == "Cerrado":
                frappe.throw(_("No se puede asignar un detalle a un período cerrado."))
            row.employer = period.employer
            row.applied_usd = money(period.applied_usd)
            self.applied_usd += row.applied_usd
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
        if not (self.docstatus == 0 and self.get("accounting_source_key")) and any(
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
                if frappe.db.get_value("CN Complementary Item", target.complementary_item, "generic_distribution"):
                    from credinomina_reconciliation.complementary_distribution import company_scope
                    complementary = frappe.get_doc("CN Complementary Item", target.complementary_item)
                    complementary.check_permission("read")
                    scope = company_scope(complementary) & allowed
                    company = (detail_row.get("employer") if detail_row else None) or target.get("employer")
                    if not company and len(scope) == 1:
                        company = next(iter(scope))
                    if not company or company not in scope:
                        frappe.throw(_("Indique una empresa de destino autorizada para la partida genérica."))
                    target.employer = company
                    target_employer = company
            if target_employer and target_employer not in allowed:
                frappe.throw(_("Un destino pertenece a una empresa no autorizada por la pagadora."))
            assigned += money(target.amount_usd)
        from credinomina_reconciliation.client_credit import load_credits
        client_reserved = sum((money(item.amount_usd) for item in load_credits([self.name])), money(0)) if self.name and self.docstatus == 1 else money(0)
        if assigned > money(equivalent) - client_reserved + MONEY_EPSILON:
            if client_reserved:
                frappe.throw(_("Los destinos usan efectivo reservado como saldo a favor. Ese saldo no está disponible para nuevos pagos."))
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
            if complementary.category in {"Saldo a favor de la empresa", "Saldo a favor del cliente"}:
                frappe.throw(_("El saldo a favor se vincula desde la partida al depósito; no se asigna como pago en Destinos."))
            if complementary.category == TOLERANCE_CATEGORY:
                frappe.throw(_("La diferencia por tolerancia ya se aplica automáticamente; no se puede agregar a Destinos."))
            if complementary.category == "Ajuste de aplicación":
                frappe.throw(_("El ajuste reduce la aplicación; no puede asignarse también como destino de un depósito."))
            if complementary.category == "Compensación entre partidas":
                frappe.throw(_("Una compensación entre partidas no es un destino de depósito."))
            if money(target.amount_usd) * money(complementary.amount_usd) <= 0 or abs(money(target.amount_usd)) > abs(money(complementary.amount_usd)):
                frappe.throw(_("El destino debe tener el signo de la partida complementaria y no superar su importe."))
            if complementary.period and frappe.db.get_value(
                "CN Reconciliation Period", complementary.period, "status"
            ) == "Cerrado":
                frappe.throw(_("El período de la partida complementaria está cerrado."))

    def before_cancel(self):
        from credinomina_reconciliation.client_credit import guard_detail_replacement
        guard_detail_replacement(self, operation="cancelar el depósito")
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
        previous = self.get_doc_before_save()
        from credinomina_reconciliation.client_credit import guard_deposit_changes
        guard_deposit_changes(self, previous)
        if previous:
            # These are server-recorded results, not editable reconciliation
            # inputs. A late detail (or a form sending empty/zero defaults)
            # must not clear cash already distributed or classified. Only the
            # explicit reconciliation writes new results directly to the DB.
            self.update({fieldname: previous.get(fieldname) for fieldname in (
                "allocated_usd", "unallocated_usd", "justified_surplus_usd",
                "unclassified_usd", "allocation_detail", "inherited_exception_comment",
                "result",
            )})
        self.deposit_reference = clean_text(self.deposit_reference)
        self.deposit_voucher = clean_text(self.deposit_voucher)
        self._validate_deposit()
        self._invalidate_changed_detail_credits()
        if previous and self._reconciliation_inputs_changed(previous):
            self.result = "Pendiente"
        from credinomina_reconciliation.detail_balances import update_detail_balances
        update_detail_balances(self)

    def _reconciliation_inputs_changed(self, previous):
        def input_value(document, fieldname):
            value = document.get(fieldname)
            if fieldname == "deposit_amount":
                return money(value)
            if fieldname == "fx_rate":
                return decimal_value(value)
            return str(value or "")

        fields = (
            "employer", "deposit_reference", "deposit_voucher", "deposit_date",
            "deposit_currency", "deposit_amount", "fx_rate",
            "detail_file", "detail_hash",
        )
        if any(
            input_value(self, fieldname) != input_value(previous, fieldname)
            for fieldname in fields
        ):
            return True
        if set(selected_periods(self)) != set(selected_periods(previous)):
            return True
        target_fields = (
            "period", "row_key", "historical_application", "complementary_item",
            "amount_usd", "detail_row", "employer",
        )
        current_targets = [
            tuple(money(target.get(fieldname)) if fieldname == "amount_usd"
                  else str(target.get(fieldname) or "") for fieldname in target_fields)
            for target in self.targets or []
        ]
        previous_targets = [
            tuple(money(target.get(fieldname)) if fieldname == "amount_usd"
                  else str(target.get(fieldname) or "") for fieldname in target_fields)
            for target in previous.targets or []
        ]
        if current_targets != previous_targets:
            return True
        detail_fields = ("name", "employer", "client", "client_number", "loan_number")
        def detail_identity(document):
            return [tuple(row.get(field) or "" for field in detail_fields)
                    for row in (document.get("detail_rows") or [])]
        return detail_identity(self) != detail_identity(previous)

    def _reconcile(self, progress=None):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
            _reconcile_sources,
        )
        if not self.employer:
            frappe.throw(_("Indique la empresa del depósito antes de conciliar."))
        return _reconcile_sources(self.employer, progress=progress)


@frappe.whitelist(methods=["POST"])
def correct_deposit_data(remittance_name: str, modified: str, employer: str,
                         deposit_date: str, deposit_reference: str, reason: str):
    """Correct submitted identity through a validated, audited server action."""
    from credinomina_reconciliation.client_credit import lock_credit_deposit
    from frappe.utils import getdate

    document = lock_credit_deposit(remittance_name)
    document.check_permission("write")
    if document.docstatus != 1:
        frappe.throw(_("Solo se pueden corregir datos de un depósito enviado."))
    if str(document.modified) != str(modified):
        frappe.throw(_("El depósito cambió. Recárguelo antes de corregir sus datos."))
    employer, deposit_reference, reason = map(clean_text, (employer, deposit_reference, reason))
    if not all((employer, deposit_date, deposit_reference, reason)):
        frappe.throw(_("Indique empresa, fecha, referencia y motivo de la corrección."))
    frappe.get_doc("CN Employer", employer).check_permission("read")
    values = {"employer": employer, "deposit_date": getdate(deposit_date),
              "deposit_reference": deposit_reference}
    changes = ["{0}: {1} → {2}".format(field, document.get(field), value)
               for field, value in values.items()
               if str(document.get(field) or "") != str(value)]
    if not changes:
        return {"name": document.name}
    document.update(values)
    # Only this endpoint may bypass Frappe's field lock. Save still runs the
    # deposit validators, credit guards, pending-result hook and versioning.
    document.flags.ignore_validate_update_after_submit = True
    document.save()
    document.add_comment("Comment", _("Corrección de datos del depósito: {0}. Motivo: {1}").format(
        "; ".join(changes), reason,
    ))
    return {"name": document.name}


@frappe.whitelist(methods=["POST"])
def create_complementary_item(remittance_name: str, modified: str, values):
    """Create, confirm and attach a complement in one permission-checked transaction."""
    from credinomina_reconciliation.client_credit import lock_credit_deposit
    document = lock_credit_deposit(remittance_name)
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
    for field in ("category", "subcategory", "voucher", "voucher_line", "posting_date", "currency", "amount",
                  "fx_rate", "period", "client_number", "loan_number", "installment_number", "description", "reason_type",
                  "credit_client", "credit_detail_row", "credit_treatment", "credit_assigned_to", "credit_commitment_date"):
        if field in values:
            item.set(field, values[field])
    item.reference = document.deposit_reference
    item.employer = document.employer
    company_credit = item.category == "Saldo a favor de la empresa"
    client_credit = item.category == "Saldo a favor del cliente"
    if client_credit:
        item.employer = ""  # The beneficiary's company may differ from the payer.
    if company_credit or client_credit:
        item.registered_deposit = document.name
    if item.period and frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
        frappe.throw(_("El período está cerrado."))
    item.flags.defer_reconciliation = True
    item.insert()
    item.submit()
    if not company_credit and not client_credit:
        document.append("targets", {
            "complementary_item": item.name, "amount_usd": item.amount_usd,
            "notes": item.description,
        })
        document.save()
    return {"name": item.name, "accounting_status": item.accounting_status,
            "company_credit": company_credit, "client_credit": client_credit, "result": item.result}


@frappe.whitelist(methods=["POST"])
def reconcile_remittance(remittance_name: str, progress_id: str = "", reason: str = ""):
    """Run reconciliation only when the user explicitly requests it."""
    document = frappe.get_doc("CN Remittance Allocation", remittance_name)
    document.check_permission("write")
    if document.docstatus != 1:
        frappe.throw(_("Confirme el depósito antes de conciliarlo."))
    document._validate_deposit()

    def progress(percent, message):
        if progress_id:
            frappe.publish_realtime(
                "cn_remittance_reconciliation_progress",
                {"remittance_name": document.name, "progress_id": progress_id,
                 "percent": percent, "message": message},
                user=frappe.session.user,
            )
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.reconciliation_audit import audit_reason
    with audit_reason("Conciliar depósito", reason):
        return reconcile_deposit(document, progress=progress)


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
            require_deduction=True, keep_zero_rows=True, require_identity=True,
        )
    except SourceFileError as exc:
        frappe.throw(str(exc), title=_("Detalle de depósito inválido"))
    for record in records:
        record["loan_number"] = normalize_credit_number(record.get("loan_number"))
    _apply_remittance_detail(document, records, content, source_url)
    return {
        "deposit": document.name,
        "rows": len(records),
        "detail_status": document.detail_status,
    }


def _apply_remittance_detail(document, records, content, source_url, origin="Archivo importado"):
    from credinomina_reconciliation.client_credit import guard_detail_replacement
    from credinomina_reconciliation.remittance_detail import detail_amount_usd
    from credinomina_reconciliation.rounding import sum_money
    guard_detail_replacement(document)
    document.set("detail_rows", [])
    # Reimport creates new row identities; prior manual evidence must be reviewed.
    for target in document.targets or []:
        target.detail_row = ""
        target.detail_row_label = ""
    allowed = allowed_employers(document.employer)
    clients = load_client_index(employers=allowed)
    loans = load_detail_loan_clients(records, clients, allowed)
    rate = flt(document.get("fx_rate")) if remittance_fx_basis(document) else 0
    amounts = []
    for record in records:
        client, identity_reason = choose_detail_client(record, clients, document.employer, allowed, loans)
        amount, explanation = detail_amount_usd(record, rate)
        amounts.append(amount)
        document.append("detail_rows", {
            key: record.get(key) for key in (
                "source_row", "row_key", "client_number", "employee_number", "client_name",
                "national_id", "loan_number", "installment_number",
                "application_reference", "comments", "application_comment",
                "deducted_usd", "deducted_nio",
            )
        } | {
            "client": client["name"] if client else "",
            "employer": client.get("employer") if client else record.get("employer") or "",
            "identity_reason": identity_reason,
            "client_number": record.get("client_number") or (client.get("client_number") if client else ""),
            "client_name": record.get("client_name") or (client.get("client_name") if client else ""),
            # Calculate the imported value without assigning cash or reconciling.
            "amount_usd": amount,
            "linked_usd": 0,
            "client_credit_usd": 0,
            "pending_usd": amount,
            "match_status": "Revisar" if not client else "Pendiente" if amount else "No deducido" if explanation == "No deducido" else "Revisar",
            "match_reason": identity_reason if not client else explanation + ("; pendiente de conciliación" if amount else ""),
            "matched_targets": "[]",
        })
    document.detail_hash = file_sha256(content)
    document.detail_source_file = source_url
    document.detail_origin = origin
    document.detail_imported_on = now_datetime()
    document.detail_count = len(records)
    document.detail_total_usd = money_float(sum_money(amounts))
    document.detail_status = "Cargado; pendiente de conciliación"
    document.save()


@frappe.whitelist()
def preview_application_detail(remittance_name: str):
    from credinomina_reconciliation.application_deposit_detail import preview_application_detail as preview
    return preview(remittance_name)


@frappe.whitelist()
def get_paying_companies(employer):
    frappe.get_doc("CN Employer", employer).check_permission("read")
    return frappe.get_list("CN Employer", filters={"name": ["in", sorted(allowed_employers(employer))]},
                           pluck="name", limit_page_length=0)


@frappe.whitelist(methods=["POST"])
def use_application_detail(remittance_name: str, fingerprint: str, replace_detail=False, selected_claim_ids=None):
    from credinomina_reconciliation.application_deposit_detail import use_application_detail as apply
    return apply(remittance_name, fingerprint, replace_detail, selected_claim_ids)


@frappe.whitelist(methods=["POST"])
def unreconcile_remittance(remittance_name: str, modified: str, reason: str):
    document = frappe.get_doc("CN Remittance Allocation", remittance_name)
    document.check_permission("write")
    if not str(reason or "").strip():
        frappe.throw(_("Indique el motivo de la desconciliación."))
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit, lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    from credinomina_reconciliation.reconciliation_audit import audit_reason
    lock_cash_pool(reconciliation_companies(document.employer))
    document.reload()
    if str(document.modified) != str(modified):
        frappe.throw(_("El depósito cambió. Recargue antes de desconciliar."))
    with audit_reason("Desconciliar depósito", reason):
        return reconcile_deposit(document, undo=True)
