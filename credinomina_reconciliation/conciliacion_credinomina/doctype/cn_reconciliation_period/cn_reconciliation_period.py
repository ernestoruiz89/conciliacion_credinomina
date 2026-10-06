from __future__ import annotations

import io
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, flt, getdate, now_datetime

from credinomina_reconciliation.aging import collection_shortfall_usd, operational_balances
from credinomina_reconciliation.application_quality import update_collection_quality
from credinomina_reconciliation.cadence import (
    MONTHLY,
    cycle_code,
    cycle_cutoff,
    cycle_for_frequency,
    cycles_conflict,
)
from credinomina_reconciliation.client_registry import ClientIndex, load_client_index, names_for_claim
from credinomina_reconciliation.deduction_recognition import recognition_reason
from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.employer_naming import (
    attach_employer_aliases,
    employer_alias_index,
    employer_label_key,
)
from credinomina_reconciliation.historical import (
    HISTORICAL_MONTHLY,
    OPERATIVE_START,
    historical_scope_interval,
    historical_scopes_conflict,
    is_historical_date,
)
from credinomina_reconciliation.parsers import (
    SourceFileError,
    clean_text,
    file_sha256,
    normalize_credit_number,
    parse_collection_file,
    source_key,
)
from credinomina_reconciliation.period_lock import current_period_write_action, period_write_action
from credinomina_reconciliation.period_naming import new_period_name, rename_period_for_context_change
from credinomina_reconciliation.period_totals import update_period_totals
from credinomina_reconciliation.reconciliation import (
    classify_deduction,
    converted_amount,
    match_collection_record,
    remittance_fx_basis,
)
from credinomina_reconciliation.rounding import CASH_EPSILON, decimal_value, money_float
from credinomina_reconciliation.templates import DETAIL_HEADERS


EXCEPTION_STATES = {
    "Deduccion parcial",
    "No deducido",
    "Deduccion en exceso",
    "Moneda no coincide",
    "Importes inconsistentes",
    "Importe invalido",
}

PENDING_REMITTANCE_DETAILS = (
    "Revisar filas", "Detalle supera depósito", "Detalle pendiente",
    "Importar detalle actualizado", "Parcial; saldo sin detalle",
    "Cargado; pendiente de conciliación",
)


class CNReconciliationPeriod(Document):
    def autoname(self):
        self.name = new_period_name(self.employer, self.payroll_month)

    def on_trash(self):
        if self.status == "Cerrado":
            frappe.throw(_("Reabra el período antes de eliminarlo."))

    def validate(self):
        self._validate_closed_transition()
        if self.payroll_month:
            self.payroll_month = getdate(self.payroll_month).replace(day=1)
        self._validate_mode()
        self._set_due_date()
        self._validate_unique_period()
        self.recalculate_totals()
        from credinomina_reconciliation.provisional_adjustments import guard_period
        guard_period(self)

    def _validate_closed_transition(self):
        previous = self.get_doc_before_save()
        action = current_period_write_action()
        if previous and previous.status == "Cerrado":
            if action == "reopen" and self.status != "Cerrado":
                return
            if action == "reconcile" and self.status == "Cerrado":
                return
            frappe.throw(_("El período está cerrado. Use Reabrir período antes de modificarlo."))
        if self.status == "Cerrado" and action != "close":
            frappe.throw(_("Use Cerrar período para completar el cierre y dejarlo bloqueado."))

    def _validate_mode(self):
        if not self.payroll_month:
            return
        previous = self.get_doc_before_save()
        linked_applications = bool(
            previous and previous.reconciliation_mode == "Historica"
            and frappe.db.exists(
                "CN Source Row", {"historical_period": self.name, "event_type": "Aplicacion"}
            )
        )
        if linked_applications and self.reconciliation_mode != "Historica":
            frappe.throw(_("Reasigne primero las aplicaciones antes de cambiar la modalidad histórica."))
        if self.reconciliation_mode == "Historica":
            self.collection_cycle = ""
            if not is_historical_date(self.payroll_month):
                frappe.throw(_("El período histórico debe estar entre abril de 2025 y agosto de 2026."))
            self.historical_scope = self.historical_scope or HISTORICAL_MONTHLY
            try:
                historical_scope_interval(
                    self.historical_scope, self.historical_application_date,
                    self.historical_start_date, self.historical_end_date,
                )
            except ValueError as exc:
                frappe.throw(_(str(exc)))
            if self.collection_rows or self.collection_file or self.employer_response_file:
                frappe.throw(_("Un período histórico no admite cobranza ni detalle de deducción."))
            if previous and previous.reconciliation_mode == "Historica":
                before = (
                    previous.employer, str(previous.payroll_month or "")[:10],
                    previous.historical_scope or HISTORICAL_MONTHLY,
                    str(previous.historical_application_date or "")[:10],
                    str(previous.historical_start_date or "")[:10],
                    str(previous.historical_end_date or "")[:10],
                )
                after = (
                    self.employer, str(self.payroll_month or "")[:10], self.historical_scope,
                    str(self.historical_application_date or "")[:10],
                    str(self.historical_start_date or "")[:10],
                    str(self.historical_end_date or "")[:10],
                )
                if before != after and linked_applications:
                    frappe.throw(_(
                        "Reasigne primero las aplicaciones antes de cambiar el corte histórico."
                    ))
        else:
            self.historical_scope = ""
            self.historical_application_date = None
            self.historical_start_date = None
            self.historical_end_date = None
            if self.is_new() and getdate(self.payroll_month) < OPERATIVE_START:
                frappe.throw(_("Para abril de 2025 a agosto de 2026 seleccione la modalidad Histórica."))
            previous = self.get_doc_before_save()
            if previous and (
                previous.employer != self.employer
                or getdate(previous.payroll_month) != getdate(self.payroll_month)
            ) and (
                previous.collection_rows or previous.status != "Borrador"
                or frappe.db.exists("CN Source Row", {"collection_period": self.name})
            ):
                frappe.throw(_(
                    "No cambie empresa ni mes después de cargar cobranza o enlazar aplicaciones. "
                    "Cree un período nuevo para el contexto correcto."
                ))
            previous_cycle = (previous.collection_cycle or MONTHLY) if previous else None
            if (
                previous and previous_cycle != (self.collection_cycle or MONTHLY)
                and (previous.collection_rows or previous.status != "Borrador")
            ):
                frappe.throw(_("No cambie el ciclo de una cobranza ya cargada."))
            if self.is_new() or (previous and previous_cycle != (self.collection_cycle or MONTHLY)):
                frequency = frappe.db.get_value(
                    "CN Employer", self.employer, "payroll_frequency"
                ) or MONTHLY
                try:
                    self.collection_cycle = cycle_for_frequency(
                        frequency, self.collection_cycle
                    )
                except ValueError as exc:
                    frappe.throw(_(str(exc)))
            elif not self.collection_cycle:
                self.collection_cycle = MONTHLY  # Existing monthly periods before this field.

    def on_update(self):
        rename_period_for_context_change(self)
        if self.flags.get("skip_comment_reconciliation"):
            return
        previous = self.get_doc_before_save()
        if not previous:
            return
        previous_rows = {row.name: row for row in previous.collection_rows or []}
        changed = any(
            row.name in previous_rows
            and any(
                (row.get(field) or "") != (previous_rows[row.name].get(field) or "")
                for field in ("first_exception_comment", "application_comment")
            )
            for row in self.collection_rows or []
        )
        if not changed:
            return
        _reconcile_if_sources(self.employer)

    def _set_due_date(self):
        if self.reconciliation_mode == "Historica":
            self.cutoff_date = None
            self.remittance_due_date = None
            return
        if not self.payroll_month or not self.employer:
            return
        self.cutoff_date = cycle_cutoff(getdate(self.payroll_month), self.collection_cycle)
        if self.collection_cycle != MONTHLY:
            if self.remittance_due_date and getdate(self.remittance_due_date) < self.cutoff_date:
                frappe.throw(_("El vencimiento del depósito no puede preceder el cierre de la quincena."))
            return
        grace_days = frappe.db.get_value("CN Employer", self.employer, "grace_days") or 10
        first_next_month = getdate(add_months(getdate(self.payroll_month).replace(day=1), 1))
        self.remittance_due_date = add_days(first_next_month, max(int(grace_days), 1) - 1)

    def _validate_unique_period(self):
        if not self.employer or not self.payroll_month:
            return
        candidates = frappe.get_all(
            self.doctype,
            filters={
                "employer": self.employer,
                "payroll_month": getdate(self.payroll_month).replace(day=1),
            },
            fields=[
                "name", "collection_cycle", "reconciliation_mode", "historical_scope",
                "historical_application_date", "historical_start_date", "historical_end_date",
            ],
            limit_page_length=1000,
        )
        for candidate in candidates:
            if candidate.name == self.name:
                continue
            if candidate.reconciliation_mode != self.reconciliation_mode:
                frappe.throw(_("Ya existe otra modalidad para esta empresa y mes."))
            if self.reconciliation_mode == "Historica":
                existing = historical_scope_interval(
                    candidate.historical_scope or HISTORICAL_MONTHLY,
                    candidate.historical_application_date,
                    candidate.historical_start_date, candidate.historical_end_date,
                )
                proposed = historical_scope_interval(
                    self.historical_scope, self.historical_application_date,
                    self.historical_start_date, self.historical_end_date,
                )
                conflict = historical_scopes_conflict(existing, proposed)
            else:
                conflict = cycles_conflict(
                    candidate.collection_cycle or MONTHLY, self.collection_cycle or MONTHLY
                )
            if conflict:
                frappe.throw(
                    _("Ya existe el período {0} para esta empresa y corte.").format(candidate.name)
                )

    def recalculate_totals(self):
        if self.reconciliation_mode == "Historica":
            update_period_totals(self)
            return  # Rebuilt from linked core applications, never from payroll rows.
        rows = list(self.collection_rows or [])
        if self.status != "Cerrado":
            update_collection_quality(rows)
        for fieldname in (
            "expected_usd",
            "expected_nio",
            "deducted_usd",
            "deducted_nio",
            "applied_usd",
            "applied_nio",
            "complementary_usd",
            "remitted_usd",
            "remitted_nio",
            "fx_variance_usd",
            "rounding_adjustment_usd",
        ):
            self.set(fieldname, sum(flt(row.get(fieldname)) for row in rows))
        if self.name and not self.is_new():
            self.exception_count = frappe.db.count(
                "CN Reconciliation Exception",
                {"period": self.name, "status": ["in", ["Abierta", "En revision"]]},
            )
        else:
            self.exception_count = 0
        update_period_totals(self)


def _attached_file(document, file_url):
    if not file_url:
        frappe.throw(_("Adjunte el archivo antes de ejecutar la importacion."))
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    if (
        file_doc.attached_to_doctype != document.doctype
        or file_doc.attached_to_name != document.name
    ):
        frappe.throw(_("El archivo debe estar adjunto a este periodo."))
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    return file_doc, content


def _assert_editable(period):
    if period.reconciliation_mode == "Historica":
        frappe.throw(_("En un período histórico importe aplicaciones y depósitos; no la cobranza."))
    if period.status == "Cerrado":
        frappe.throw(_("Un periodo cerrado no se puede volver a importar."))


def _recognition_pairs():
    paired = []
    for item in frappe.get_all(
        "CN Remittance Allocation",
        filters={"docstatus": 1},
        fields=[
            "name", "employer", "deposit_reference", "deposit_voucher",
            "deposit_date", "deposit_currency", "deposit_amount", "amount_usd",
            "fx_rate", "notes", "support_file", "allocated_usd", "unallocated_usd",
        ],
        limit_page_length=100000,
    ):
        fx_basis = remittance_fx_basis(item) if item.deposit_currency == "NIO" else "Moneda original USD"
        deposit = frappe._dict(
            name=item.name, reference=item.deposit_reference,
            voucher=item.deposit_voucher, event_date=item.deposit_date,
            employer_text=item.employer, currency=item.deposit_currency,
            amount=item.deposit_amount, equivalent_currency="USD",
            equivalent_amount=item.amount_usd,
            fx_basis=fx_basis,
            manual_fx_rate=item.fx_rate,
            allocated_usd=item.allocated_usd, justified_surplus_usd=0,
        )
        paired.append((deposit, deposit))
    return paired


def _recognition_candidates(period):
    _assert_editable(period)
    if not period.collection_rows or period.employer_response_file:
        return []
    if period.deduction_basis:
        return []
    used = {
        item.deduction_recognition_deposit
        for item in frappe.get_all(
            "CN Reconciliation Period",
            fields=["name", "deduction_recognition_deposit"],
            limit_page_length=100000,
        )
        if item.name != period.name and item.deduction_recognition_deposit
    }
    known_employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    attach_employer_aliases(known_employers)
    employer_by_label, ambiguous_labels = employer_alias_index(known_employers)
    rows = [row.as_dict() for row in period.collection_rows]
    candidates = []
    for account, bank in _recognition_pairs():
        if account.name in used:
            continue
        source_labels = {
            employer_label_key(item.employer_text)
            for item in (account, bank) if clean_text(item.employer_text)
        }
        if source_labels & ambiguous_labels:
            continue
        labels = {
            employer_by_label.get(label) for label in source_labels
        }
        if any(label and label != period.employer for label in labels):
            continue
        if not account.event_date and not bank.event_date:
            continue
        event_date = getdate(account.event_date or bank.event_date)
        if period.cutoff_date and event_date < getdate(period.cutoff_date):
            continue
        if recognition_reason(rows, account, bank):
            continue
        usd = converted_amount(account, "USD")
        if usd is None:
            usd = converted_amount(bank, "USD")
        candidates.append({
            "source_row_id": account.name,
            "bank_row_id": bank.name,
            "reference": account.reference,
            "voucher": account.voucher,
            "event_date": str(event_date),
            "currency": account.currency,
            "amount": flt(account.amount),
            "amount_usd": money_float(usd),
            "account": account,
            "bank": bank,
        })
    return candidates


@frappe.whitelist()
def get_recognizable_deposits(period_name: str):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    if not (frappe.has_permission("CN Accounting Import", "read") or frappe.has_permission("CN Remittance Allocation", "read")):
        frappe.throw(_("No tiene permiso para consultar depósitos."))
    return [
        {key: value for key, value in candidate.items() if key not in {"account", "bank"}}
        for candidate in _recognition_candidates(period)
    ]


@frappe.whitelist(methods=["POST"])
def recognize_collection_from_deposit(period_name: str, source_row_id: str, justification: str):
    # Serialize competing recognitions of the same cash row, then recheck all
    # conditions inside the current transaction.
    lock_table = "tabCN Remittance Allocation" if frappe.db.exists(
        "CN Remittance Allocation", source_row_id
    ) else "tabCN Source Row"
    frappe.db.sql(
        f"SELECT name FROM `{lock_table}` WHERE name = %s FOR UPDATE",
        (source_row_id,),
    )
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    if not (frappe.has_permission("CN Accounting Import", "read") or frappe.has_permission("CN Remittance Allocation", "read")):
        frappe.throw(_("No tiene permiso para consultar depósitos."))
    justification = clean_text(justification)
    if len(justification) < 12:
        frappe.throw(_("Explique por qué el depósito permite reconocer la cobranza completa (mínimo 12 caracteres)."))
    matches = [
        candidate for candidate in _recognition_candidates(period)
        if candidate["source_row_id"] == source_row_id
    ]
    if len(matches) != 1:
        frappe.throw(_("El depósito no coincide de forma única con esta cobranza o ya fue usado."))
    chosen = matches[0]
    evidence_date = getdate(chosen["event_date"])
    if period.deduction_evidence_date:
        period.notes = _append_note(
            period.notes,
            _("Fecha de evidencia de planilla previamente indicada ({0}); no usada para la inferencia por depósito.").format(
                display_date(period.deduction_evidence_date)
            ),
        )
    period.deduction_evidence_date = None
    for row in period.collection_rows:
        row.deducted_usd = flt(row.expected_usd)
        row.deducted_nio = flt(row.expected_nio)
        row.deduction_currency = ""  # The payment currency does not prove payroll currency.
        row.deduction_status = "Inferida por depósito"
        row.deduction_evidence_date = evidence_date
        row.deduction_match_note = _(
            "Deducción inferida, no confirmada por planilla. Depósito {0}; respaldo: {1}."
        ).format(chosen["reference"], chosen["source_row_id"])
    period.deduction_basis = "Depósito coincidente"
    period.deduction_recognition_deposit = chosen["source_row_id"]
    period.deduction_recognition_reference = chosen["reference"]
    period.deduction_recognition_note = justification
    period.deduction_recognition_on = now_datetime()
    period.deduction_recognition_by = frappe.session.user
    period.status = "Pendiente"
    period.notes = _append_note(
        period.notes,
        _("Cobranza reconocida provisionalmente por depósito {0} ({1}) el {2} por {3}: {4}").format(
            chosen["reference"], chosen["source_row_id"],
            period.deduction_recognition_on, frappe.session.user, justification,
        ),
    )
    period.flags.skip_comment_reconciliation = True
    period.save()
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
        _reconcile_sources,
    )

    summary = _reconcile_sources(period.employer)
    return {"period": period.name, "source_reconciliation": summary}


@frappe.whitelist(methods=["POST"])
def revert_deposit_recognition(period_name: str):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    _assert_editable(period)
    if period.deduction_basis != "Depósito coincidente":
        frappe.throw(_("Este período no usa un depósito como detalle de deducción."))
    old_reference = period.deduction_recognition_reference
    _clear_deposit_recognition(period)
    for row in period.collection_rows:
        row.deducted_usd = 0
        row.deducted_nio = 0
        row.deduction_currency = ""
        row.deduction_status = "Pendiente de detalle"
        row.deduction_evidence_date = None
        row.deduction_match_note = ""
    period.status = "Pendiente"
    period.notes = _append_note(
        period.notes,
        _("Reconocimiento provisional por depósito {0} revertido el {1} por {2}.").format(
            old_reference, now_datetime(), frappe.session.user,
        ),
    )
    period.flags.skip_comment_reconciliation = True
    period.save()
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
        _reconcile_sources,
    )

    summary = _reconcile_sources(period.employer)
    return {"period": period.name, "source_reconciliation": summary}


def _clear_deposit_recognition(period):
    period.deduction_recognition_deposit = ""
    period.deduction_recognition_reference = ""
    period.deduction_recognition_note = ""
    period.deduction_recognition_on = None
    period.deduction_recognition_by = ""
    period.deduction_basis = ""


@frappe.whitelist(methods=["POST"])
def import_collection(period_name: str):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    _assert_editable(period)
    if period.deduction_basis == "Depósito coincidente":
        frappe.throw(_("Revierta primero el reconocimiento por depósito antes de reemplazar la cobranza."))
    file_doc, content = _attached_file(period, period.collection_file)
    try:
        parsed = parse_collection_file(file_doc.file_name, content, require_name=True)
    except SourceFileError as exc:
        frappe.throw(str(exc), title=_("Archivo de cobranza invalido"))

    for record in parsed:
        record["loan_number"] = normalize_credit_number(record.get("loan_number"))

    import_hash = file_sha256(content)
    needs_credit_normalization = any(
        row.loan_number != normalize_credit_number(row.loan_number)
        for row in period.collection_rows
    )
    if period.collection_import_sha256 == import_hash and period.collection_rows and not needs_credit_normalization:
        source_summary = _reconcile_if_sources(period.employer)
        return {
            "period": period.name, "rows": len(period.collection_rows),
            "status": period.status, "unchanged": True,
            "source_reconciliation": source_summary,
        }

    existing = {row.row_key: row for row in period.collection_rows if row.row_key}
    # Previously imported numeric credits have opaque row keys generated before
    # adding -1. Keep those keys and child IDs so targets and exceptions survive.
    existing_by_identity = {}
    for row in existing.values():
        existing_by_identity.setdefault(_collection_identity_key(period, row), []).append(row)
    ordered_rows = []
    clients = ClientIndex()
    seen = set()
    amount_changed = False
    source_fields = (
        "source_row", "client_number", "employee_number", "client_name",
        "national_id", "loan_number", "installment_number", "total_installments",
        "expected_usd", "expected_nio", "comments",
    )
    for record in parsed:
        if not record.get("loan_number"):
            frappe.throw(_("La fila {0} de cobranza no tiene número de crédito.").format(record["source_row"]))
        client = clients.ensure_from_collection(record, period.employer)
        row_key = record.get("row_key") or _collection_identity_key(period, record)
        if not record.get("row_key") and row_key not in existing:
            candidates = existing_by_identity.get(row_key, [])
            if len(candidates) > 1:
                frappe.throw(_(
                    "La fila {0} coincide con varias cuotas existentes al normalizar el crédito. "
                    "Indique Fila ID para identificar la cuota sin alterar sus vínculos."
                ).format(record["source_row"]))
            if candidates:
                row_key = candidates[0].row_key
        if row_key in seen:
            frappe.throw(
                _("La fila {0} duplica cliente, credito y cuota.").format(
                    record["source_row"]
                )
            )
        seen.add(row_key)
        row = existing.get(row_key)
        if row:
            amount_changed |= any(
                abs(flt(row.get(field)) - flt(record.get(field))) > CASH_EPSILON
                for field in ("expected_usd", "expected_nio")
            )
            row.update({field: record.get(field) for field in source_fields})
            row.client = client
            # Empty cells in a corrected cobranza are not instructions to erase
            # comments or references already entered by an operator.
            for field in ("application_reference", "application_comment"):
                if record.get(field):
                    row.set(field, record[field])
        else:
            row = period.append(
                "collection_rows",
                {
                    **record,
                    "client": client,
                    "row_key": row_key,
                    "deduction_status": "Pendiente de detalle",
                    "application_status": "Pendiente",
                },
            )
        ordered_rows.append(row)
    removed = [row for key, row in existing.items() if key not in seen]
    if any(
        any(abs(flt(row.get(field))) > CASH_EPSILON for field in (
            "applied_usd", "complementary_usd", "remitted_usd",
            "rounding_adjustment_usd", "fx_variance_usd",
        ))
        for row in removed
    ):
        frappe.throw(_(
            "La nueva cobranza elimina cuotas con aplicaciones o depósitos relacionados. "
            "Revise esos enlaces antes de reemplazarla."
        ))
    if amount_changed and period.deduction_basis == "Detalle de empresa" and not period.employer_response_file:
        frappe.throw(_(
            "La cobranza cambia importes ya comparados. Adjunte el detalle de empresa "
            "para recalcular la deducción junto con la nueva cobranza."
        ))
    period.set("collection_rows", ordered_rows)
    for index, row in enumerate(period.collection_rows, 1):
        row.idx = index
    period.collection_import_sha256 = import_hash
    if not period.employer_response_file:
        period.status = "Pendiente"
    period.notes = _append_note(
        period.notes,
        _("Cobranza importada: {0} filas; SHA-256 {1}.").format(
            len(parsed), import_hash
        ),
    )
    period.flags.skip_comment_reconciliation = True
    period.save()
    # Files can arrive in any order. A corrected cobranza must be compared again
    # with the existing employer detail before reconciling core applications.
    detail_result = (
        import_employer_response(period.name)
        if period.employer_response_file and period.deduction_evidence_date
        else None
    )
    source_summary = (
        detail_result.get("source_reconciliation") if detail_result
        else _reconcile_if_sources(period.employer)
    )
    period.reload()
    return {
        "period": period.name, "rows": len(parsed), "status": period.status,
        "detail_import": detail_result, "source_reconciliation": source_summary,
    }


def _collection_identity_key(period, record):
    return source_key(
        period.employer,
        getdate(period.payroll_month).replace(day=1),
        period.collection_cycle,
        record.get("client_number") or record.get("national_id") or record.get("employee_number") or record.get("client_name"),
        normalize_credit_number(record.get("loan_number")),
        record.get("installment_number"),
    )[:24]


@frappe.whitelist(methods=["POST"])
def recognize_collection_as_employer_detail(period_name: str, evidence_date: str, confirmed: int = 0):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    frappe.db.sql("SELECT name FROM `tabCN Reconciliation Period` WHERE name = %s FOR UPDATE", (period_name,))
    period.reload()
    _assert_editable(period)
    if str(confirmed) != "1":
        frappe.throw(_("Confirme que la empresa dedujo la cobranza completa."))
    if not evidence_date:
        frappe.throw(_("Indique la fecha de evidencia de deducción."))
    evidence_date = getdate(evidence_date)
    if not period.collection_rows:
        frappe.throw(_("Cargue primero la cobranza del período."))
    if period.deduction_basis or period.employer_response_file or any(
        flt(row.deducted_usd) or flt(row.deducted_nio)
        or row.deduction_status not in (None, "", "Pendiente de detalle")
        for row in period.collection_rows
    ):
        frappe.throw(_("El período ya tiene detalle o deducciones registradas; no se sobrescribirán."))
    if any(flt(row.expected_usd) < 0 or flt(row.expected_nio) < 0 for row in period.collection_rows):
        frappe.throw(_("Revise los importes negativos de la cobranza antes de reconocerla."))
    if not any(money_float(row.expected_usd) or money_float(row.expected_nio) for row in period.collection_rows):
        frappe.throw(_("La cobranza no tiene importes para reconocer."))
    note = _("Cobranza reconocida como detalle de la empresa el {0} por {1}; deducción completa confirmada por el usuario.").format(now_datetime(), frappe.session.user)
    for row in period.collection_rows:
        row.deducted_usd = money_float(row.expected_usd)
        row.deducted_nio = money_float(row.expected_nio)
        row.deduction_currency = (
            "Ambas" if row.deducted_usd and row.deducted_nio
            else "USD" if row.deducted_usd else "NIO" if row.deducted_nio else ""
        )
        row.deduction_status = classify_deduction(
            expected_usd=row.expected_usd, expected_nio=row.expected_nio,
            deducted_usd=row.deducted_usd, deducted_nio=row.deducted_nio,
        )
        row.deduction_evidence_date = evidence_date
        row.deduction_match_note = note
    period.deduction_basis = "Detalle de empresa"
    period.deduction_evidence_date = evidence_date
    period.employer_response_import_key = ""
    period.status = "Pendiente"
    period.notes = _append_note(period.notes, note)
    period.flags.skip_comment_reconciliation = True
    period.save()
    source_summary = _reconcile_if_sources(period.employer)
    return {"period": period.name, "rows": len(period.collection_rows), "source_reconciliation": source_summary}


@frappe.whitelist(methods=["POST"])
def import_employer_response(period_name: str):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    _assert_editable(period)
    if not period.collection_rows:
        frappe.throw(_("Importe primero el archivo de cobranza."))
    if not period.deduction_evidence_date:
        frappe.throw(
            _(
                "Indique la fecha de evidencia de deduccion antes de importar el detalle de la empresa."
            )
        )
    file_doc, content = _attached_file(period, period.employer_response_file)
    try:
        responses = parse_collection_file(
            file_doc.file_name, content,
            require_deduction=True, require_name=True, keep_zero_rows=True,
        )
    except SourceFileError as exc:
        frappe.throw(str(exc), title=_("Detalle de empresa invalido"))

    for response in responses:
        response["loan_number"] = normalize_credit_number(response.get("loan_number"))
    needs_credit_normalization = any(
        row.loan_number != normalize_credit_number(row.loan_number)
        for row in period.collection_rows
    )
    for row in period.collection_rows:
        row.loan_number = normalize_credit_number(row.loan_number)

    client_catalog = load_client_index()
    import_key = _employer_response_import_key(period, content, client_catalog)
    if (
        period.employer_response_import_key == import_key
        and period.deduction_basis == "Detalle de empresa"
        and not needs_credit_normalization
    ):
        source_summary = _reconcile_if_sources(period.employer)
        return {
            "period": period.name, "status": period.status,
            "unchanged": True, "source_reconciliation": source_summary,
        }

    if period.deduction_basis == "Depósito coincidente":
        period.notes = _append_note(
            period.notes,
            _("El detalle real de la empresa reemplazó el reconocimiento provisional por depósito {0}.").format(
                period.deduction_recognition_reference
            ),
        )
        _clear_deposit_recognition(period)
        for row in period.collection_rows:
            if row.deduction_status == "Inferida por depósito":
                row.deducted_usd = 0
                row.deducted_nio = 0
                row.deduction_currency = ""
                row.deduction_status = "Pendiente de detalle"
                row.deduction_evidence_date = None
                row.deduction_match_note = ""
    period.deduction_basis = "Detalle de empresa"

    rows_by_name = {row.name: row for row in period.collection_rows}
    by_client = {client["name"]: client for client in client_catalog}
    candidates = []
    for row in period.collection_rows:
        candidate = {**row.as_dict(), "name": row.name}
        linked = by_client.get(row.get("client"))
        candidate["client_aliases"] = (
            [linked["client_name"], *(linked.get("client_aliases") or ())]
            if linked else names_for_claim(candidate, client_catalog, period.employer)
        )
        candidates.append(candidate)
    matched_names = set()
    seen_exceptions = set()
    unmatched_keys = set()
    unmatched = 0
    for response in responses:
        match, reason = match_collection_record(response, candidates)
        if not match or match["name"] in matched_names:
            unmatched += 1
            exceptional_identity = (
                response.get("row_key") or response.get("client_number")
                or response.get("national_id") or response.get("employee_number")
                or response.get("client_name"),
                response.get("loan_number"), response.get("installment_number"),
            )
            exception_key = _deduction_exception_key(
                period.name, "Detalle de empresa sin coincidencia", *exceptional_identity,
            )
            if exception_key in unmatched_keys:
                exception_key = _deduction_exception_key(
                    period.name, "Detalle de empresa sin coincidencia",
                    *exceptional_identity, response.get("source_row"),
                )
            unmatched_keys.add(exception_key)
            seen_exceptions.add(_create_exception(
                period,
                exception_type="Detalle de empresa sin coincidencia",
                exception_key=exception_key,
                source_row=response.get("source_row"),
                client_number=response.get("client_number"),
                loan_number=response.get("loan_number"),
                amount_usd=response.get("deducted_usd"),
                amount_nio=response.get("deducted_nio"),
                description=(
                    "Fila duplicada en la respuesta"
                    if match and match["name"] in matched_names
                    else reason
                ),
            ))
            continue
        row = rows_by_name[match["name"]]
        matched_names.add(row.name)
        deducted_usd, deducted_nio, conversion_note = _deduction_equivalents(
            row, response
        )
        row.deducted_usd = deducted_usd
        row.deducted_nio = deducted_nio
        if flt(response.get("deducted_usd")) and flt(response.get("deducted_nio")):
            row.deduction_currency = "Ambas"
        elif flt(response.get("deducted_nio")):
            row.deduction_currency = "NIO"
        elif flt(response.get("deducted_usd")):
            row.deduction_currency = "USD"
        else:
            row.deduction_currency = ""
        row.deduction_evidence_date = period.deduction_evidence_date
        row.deduction_match_note = " ".join(
            part
            for part in (
                _("Coincidencia exacta por {0}.").format(reason),
                conversion_note,
            )
            if part
        )
        row.deduction_status = classify_deduction(
            expected_usd=row.expected_usd,
            expected_nio=row.expected_nio,
            deducted_usd=row.deducted_usd,
            deducted_nio=row.deducted_nio,
        )
        if response.get("application_reference"):
            row.application_reference = response["application_reference"]
        if response.get("application_comment"):
            row.application_comment = response["application_comment"]
        if row.deduction_status in EXCEPTION_STATES:
            seen_exceptions.add(_create_exception(
                period,
                exception_type=row.deduction_status,
                exception_key=_deduction_exception_key(
                    period.name, row.row_key or row.name,
                ),
                source_row=response.get("source_row"),
                client_number=row.client_number,
                loan_number=row.loan_number,
                amount_usd=row.deducted_usd,
                amount_nio=row.deducted_nio,
                description=row.application_comment or row.comments,
                collection_row_id=row.name,
            ))

    missing = 0
    for row in period.collection_rows:
        if row.name not in matched_names:
            missing += 1
            row.deduction_status = "Pendiente de detalle"
            seen_exceptions.add(_create_exception(
                period,
                exception_type="Pendiente de detalle de empresa",
                exception_key=_deduction_exception_key(
                    period.name, row.row_key or row.name,
                ),
                client_number=row.client_number,
                loan_number=row.loan_number,
                amount_usd=row.expected_usd,
                amount_nio=row.expected_nio,
                description=_(
                    "La respuesta de la empresa no incluyo esta fila de cobranza."
                ),
                collection_row_id=row.name,
            ))

    _retire_obsolete_deduction_exceptions(period.name, seen_exceptions)
    period.status = _deduction_stage_status(period.collection_rows, unmatched, missing)
    period.employer_response_import_key = import_key
    period.notes = _append_note(
        period.notes,
        _(
            "Detalle de empresa importado: {0} coincidencias, {1} filas sin enlace y {2} cuotas ausentes."
        ).format(
            len(matched_names), unmatched, missing
        ),
    )
    period.flags.skip_comment_reconciliation = True
    period.save()
    source_summary = _reconcile_if_sources(period.employer)
    if source_summary is not None:
        period.reload()
    return {
        "period": period.name,
        "matched": len(matched_names),
        "unmatched": unmatched,
        "missing": missing,
        "status": period.status,
        "source_reconciliation": source_summary,
    }


def _pending_registered_targets(target_filters):
    from credinomina_reconciliation.period_closure import pending_registered_targets
    return pending_registered_targets(target_filters)


def _has_operative_application(period, scope=None):
    from credinomina_reconciliation.period_closure import ClosureScope
    return (scope or ClosureScope(period)).has_operative_application()


def _pending_remittance_details_for_period(period, scope=None):
    """Find problematic cash detail linked by period, target or actual allocation."""
    from credinomina_reconciliation.period_closure import ClosureScope
    scope = scope or ClosureScope(period)
    return sorted(deposit.name for deposit in scope.related_deposits()
                  if deposit.detail_status in PENDING_REMITTANCE_DETAILS)


def _control_cut_summary(period, open_exceptions, pending_details):
    """A dated operational snapshot, not a claim that balances are settled."""
    rows = list(period.collection_rows or []) if period.reconciliation_mode != "Historica" else []
    awaiting_detail = sum(
        row.deduction_status in (None, "", "Pendiente de detalle") for row in rows
    )
    worker_receivable = sum(
        collection_shortfall_usd(row) or 0 for row in rows
    )
    deducted_unremitted = sum(
        balance["amount_usd"]
        for row in rows
        for balance in operational_balances(row, {
            "cutoff_date": getattr(period, "cutoff_date", None),
            "remittance_due_date": getattr(period, "remittance_due_date", None),
        })
        if balance["balance_type"] == "Deducido sin depósito asignado"
    )
    unsettled_rows = sum(
        row.application_status != "Aplicado y remitido" for row in rows
    )
    figures = (
        f"Cobranza US$ {flt(period.expected_usd):.2f}; "
        f"deducido US$ {flt(period.deducted_usd):.2f}; "
        f"aplicado US$ {flt(period.applied_usd):.2f}; "
        f"depositado US$ {flt(period.remitted_usd):.2f}; "
        f"excepciones abiertas {open_exceptions}; "
        f"detalles de depósito pendientes {pending_details}"
    )
    if period.reconciliation_mode == "Historica":
        return figures
    return (
        f"{figures}; cuotas sin detalle {awaiting_detail}; "
        f"cuotas sin liquidar {unsettled_rows}; "
        f"Cobranza no deducida confirmada US$ {worker_receivable:.2f}; "
        f"deducido sin depósito asignado US$ {deducted_unremitted:.2f}"
    )


@frappe.whitelist(methods=["POST"])
def record_control_cut(period_name: str, note: str):
    """Record month-end evidence while keeping late detail, applications and cash admissible."""
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    if period.status == "Cerrado":
        frappe.throw(_("El período ya está cerrado. Reábralo antes de registrar otro corte."))
    note = clean_text(note)
    if len(note) < 12:
        frappe.throw(_("Indique el motivo y la siguiente gestión del corte (mínimo 12 caracteres)."))
    if _reconcile_if_sources(period.employer) is not None:
        period.reload()
    if period.status == "Borrador" and not period.collection_rows:
        frappe.throw(_("Cargue la cobranza o las aplicaciones históricas antes del corte."))
    period.recalculate_totals()
    open_exceptions = frappe.db.count(
        "CN Reconciliation Exception",
        {"period": period.name, "status": ["in", ["Abierta", "En revision"]]},
    )
    pending_details = len(_pending_remittance_details_for_period(period))
    summary = _control_cut_summary(period, open_exceptions, pending_details)
    period.control_cut_on = now_datetime().replace(microsecond=0)
    period.control_cut_by = frappe.session.user
    period.control_cut_note = note
    period.control_cut_summary = summary
    period.notes = _append_note(
        period.notes,
        _("CORTE DE CONTROL {0} por {1}. {2}. Seguimiento: {3}. El período permanece abierto.").format(
            period.control_cut_on, period.control_cut_by, summary, note,
        ),
    )
    period.flags.skip_comment_reconciliation = True
    period.save()
    from credinomina_reconciliation.control_cuts import save_cut
    evidence = save_cut(period)
    return {
        "period": period.name, "status": period.status,
        "control_cut_on": period.control_cut_on, "summary": summary,
        **evidence,
    }


@frappe.whitelist(methods=["POST"])
def close_period(period_name: str, progress_id: str = ""):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    if period.status == "Cerrado":
        frappe.throw(_("Este período ya está cerrado."))
    if not period.employer:
        frappe.throw(_("Indique la empresa del período antes de cerrarlo."))

    def report(percent, message):
        if progress_id:
            frappe.publish_realtime("cn_period_closure_progress", {
                "period_name": period.name, "progress_id": progress_id,
                "percent": percent, "message": message,
            }, user=frappe.session.user)
    # Closure must evaluate the latest imports, not the totals left by the
    # last manual reconciliation. A late core edit can change both balances
    # and open exceptions.
    report(5, _("Revisando movimientos recientes de la empresa y sus vínculos financieros…"))
    def reconcile_progress(percent, message):
        report(5 + int(percent * 0.65), message)

    if _reconcile_if_sources(
        period.employer, progress=reconcile_progress, preserve_deposits=True,
    ) is not None:
        period.reload()
    if period.status == "Cerrado":
        frappe.throw(_("Este período ya está cerrado."))
    from credinomina_reconciliation.period_closure import ClosureScope
    scope = ClosureScope(period)
    report(75, _("Validando los depósitos y excepciones relacionados con este período…"))
    if period.reconciliation_mode != "Historica" and not period.collection_rows:
        frappe.throw(_("Cargue la cobranza antes de cerrar el período operativo."))
    if _pending_remittance_details_for_period(period, scope=scope):
        frappe.throw(_("Hay detalles de depósito por cliente pendientes de revisión para este período."))
    if frappe.db.count(
        "CN Reconciliation Exception",
        {"period": period.name, "status": ["in", ["Abierta", "En revision"]]},
    ):
        frappe.throw(_("Resuelva las excepciones antes de cerrar el periodo."))
    if period.reconciliation_mode == "Historica":
        application_ids = scope.application_ids()
        if not application_ids:
            frappe.throw(_(
                "No se puede cerrar un período histórico sin aplicaciones efectivas asignadas."
            ))
        if period.status != "Conciliado":
            frappe.throw(_("El aplicado neto debe estar cubierto por depósitos o compensado por ajustes confirmados antes del cierre."))
        if application_ids and _pending_registered_targets(
            {"historical_application": ["in", application_ids]}
        ):
            frappe.throw(_("Hay destinos de depósitos históricos pendientes o inválidos."))
        if frappe.db.count(
            "CN Complementary Item",
            {"docstatus": 1, "category": ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]], "period": period.name, "result": ["!=", "Saldo a favor documentado"]},
        ):
            frappe.throw(_("Hay excedentes históricos pendientes de validar."))
        report(95, _("Guardando el resultado y bloqueando el período…"))
        _mark_period_closed(period)
        report(100, _("Período cerrado."))
        return {"period": period.name, "status": period.status}
    period.recalculate_totals()
    if not _has_operative_application(period, scope=scope):
        frappe.throw(_(
            "No se puede cerrar el período sin aplicaciones efectivas del core enlazadas a la cobranza."
        ))
    if _pending_registered_targets({"period": period.name}):
        frappe.throw(_("Hay destinos de depósitos pendientes o inválidos para este período."))
    if frappe.db.count(
        "CN Complementary Item",
        {
            "docstatus": 1, "category": ["in", ["Saldo a favor de la empresa", "Saldo a favor del cliente"]], "period": period.name,
            "result": ["!=", "Saldo a favor documentado"],
        },
    ):
        frappe.throw(_("Hay excedentes de deposito pendientes de validar."))
    references = {
        row.application_reference
        for row in period.collection_rows if row.application_reference
    }
    references.update(
        frappe.get_all(
            "CN Source Row",
            filters={"event_type": "Aplicacion", "collection_period": period.name, "effective": 1},
            pluck="reference",
        )
    )
    target_deposits = frappe.get_all(
        "CN Remittance Target", filters={"period": period.name},
        pluck="parent", limit_page_length=100000,
    )
    if target_deposits:
        references.update(frappe.get_all(
            "CN Remittance Allocation",
            filters={"name": ["in", target_deposits], "docstatus": 1},
            pluck="deposit_reference", limit_page_length=100000,
        ))
    references.discard("")
    references.discard(None)
    if references:
        if scope.has_unclassified_source_deposit(references):
            frappe.throw(
                _("Hay depositos relacionados con este periodo sin distribuir ni justificar.")
            )
    for deposit in scope.related_deposits():
        if flt(deposit.unclassified_usd) > CASH_EPSILON:
            frappe.throw(_("El depósito {0} tiene un saldo sin clasificar relacionado con este período.").format(deposit.name))
    if any(abs(flt(row.fx_variance_usd)) > 0.01 for row in period.collection_rows):
        frappe.throw(
            _("Hay diferencias cambiarias pendientes de revisar y aplicar en el core.")
        )
    if any(
        collection_shortfall_usd(row) is None
        or collection_shortfall_usd(row) > CASH_EPSILON
        for row in period.collection_rows
    ):
        frappe.throw(_(
            "Hay diferencias de cobranza o deducciones sin confirmar en la primera conciliación; no representan CxC por sí mismas. "
            "Registre un corte de control y mantenga abierto el período para su seguimiento."
        ))
    if any(row.application_status != "Aplicado y remitido" for row in period.collection_rows):
        frappe.throw(_(
            "El cierre definitivo requiere todas las filas aplicadas y remitidas. "
            "Si hay cuotas no deducidas o pagos pendientes, registre un corte de control y continúe el seguimiento."
        ))
    report(95, _("Guardando el resultado y bloqueando el período…"))
    _mark_period_closed(period)
    report(100, _("Período cerrado."))
    return {"period": period.name, "status": period.status}


def _mark_period_closed(period):
    period.status_before_close = period.status
    period.closed_on = now_datetime()
    period.closed_by = frappe.session.user
    period.status = "Cerrado"
    period.flags.skip_comment_reconciliation = True
    with period_write_action("close"):
        period.save()


@frappe.whitelist(methods=["POST"])
def reopen_period(period_name: str, reason: str):
    frappe.only_for(["System Manager", "Supervisor Credinomina"])
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("write")
    if period.status != "Cerrado":
        frappe.throw(_("Solo se puede reabrir un período cerrado."))
    reason = clean_text(reason)
    if not reason:
        frappe.throw(_("Indique el motivo de la reapertura."))
    previous_status = clean_text(period.status_before_close)
    period.status = (
        previous_status if previous_status and previous_status != "Cerrado"
        else "Conciliado"
    )
    period.reopened_on = now_datetime()
    period.reopened_by = frappe.session.user
    period.reopen_reason = reason
    with period_write_action("reopen"):
        period.save()
    return {"period": period.name, "status": period.status}


@frappe.whitelist(methods=["POST"])
def export_collection(period_name: str):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    if period.reconciliation_mode == "Historica":
        frappe.throw(_("La modalidad histórica no genera archivo de cobranza."))
    if not period.collection_rows:
        frappe.throw(_("El periodo no tiene detalle para exportar."))
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        frappe.throw(_("Se requiere openpyxl para exportar el archivo."))

    headers = DETAIL_HEADERS
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cobranza"
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in period.collection_rows:
        sheet.append(
            [
                row.client_number,
                row.employee_number,
                row.client_name,
                row.national_id,
                row.loan_number,
                row.installment_number,
                row.total_installments,
                flt(row.expected_usd),
                flt(row.expected_nio),
                row.comments,
                row.application_reference,
                row.application_comment,
                None,
                None,
                row.row_key,
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [15, 17, 38, 20, 16, 12, 20, 22, 22, 32, 26, 32, 18, 18, 28]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    stream = io.BytesIO()
    workbook.save(stream)
    content = stream.getvalue()
    file_name = f"cobranza_{period.name}_{cycle_code(period.collection_cycle or MONTHLY)}.xlsx"
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": file_name,
            "attached_to_doctype": period.doctype,
            "attached_to_name": period.name,
            "is_private": 1,
            "content": content,
        }
    ).insert(ignore_permissions=True)
    return {"file_url": file_doc.file_url, "file_name": file_name}


def _reconcile_if_sources(employer, progress=None, preserve_deposits=False):
    if not employer:
        frappe.throw(_("Indique la empresa antes de actualizar sus conciliaciones."))
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    companies = reconciliation_companies(employer)
    scope = {"employer": ["in", companies]}
    imported_core = frappe.db.exists(
        "CN Accounting Import",
        {**scope, "status": ["in", ["Importado", "Importado con excepciones"]]},
    )
    confirmed_cash = frappe.db.exists(
        "CN Remittance Allocation", {**scope, "docstatus": 1},
    )
    confirmed_complement = frappe.db.exists(
        "CN Complementary Item", {**scope, "docstatus": 1},
    )
    if not (imported_core or confirmed_cash or confirmed_complement):
        return None
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
    if preserve_deposits:
        return _reconcile_sources(
            employer, progress=progress, preserve_deposits=True,
        )
    return _reconcile_sources(employer, progress=progress)


def _deduction_stage_status(rows, unmatched, missing):
    # Deduction evidence alone never proves reconciliation with the core/cash.
    return "Pendiente"


def _employer_response_import_key(period, content, client_catalog=()):
    identities = sorted(
        (
            clean_text(row.row_key or row.name),
            clean_text(row.client), clean_text(row.client_number),
            clean_text(row.employee_number), clean_text(row.client_name),
            clean_text(row.national_id), clean_text(row.loan_number),
            clean_text(row.installment_number), flt(row.expected_usd),
            flt(row.expected_nio),
        )
        for row in period.collection_rows
    )
    linked_names = {row.client for row in period.collection_rows if row.client}
    include_all_employer_clients = len(linked_names) < len(period.collection_rows)
    relevant_clients = sorted(
        (
            clean_text(client["name"]), clean_text(client["client_name"]),
            clean_text(client.get("client_number")),
            clean_text(client.get("employee_number")),
            clean_text(client.get("national_id")),
            tuple(sorted(clean_text(alias) for alias in client.get("client_aliases") or ())),
        )
        for client in client_catalog
        if client.get("employer") == period.employer
        and (include_all_employer_clients or client["name"] in linked_names)
    )
    return source_key(
        "credit-suffix-1",
        file_sha256(content), period.deduction_evidence_date,
        period.collection_import_sha256,
        json.dumps((identities, relevant_clients), ensure_ascii=False),
    )


def _deduction_exception_key(*parts):
    return "DED-" + source_key(*parts)[:40]


def _create_exception(
    period,
    *,
    exception_type,
    exception_key,
    source_row=None,
    client_number=None,
    loan_number=None,
    amount_usd=0,
    amount_nio=0,
    description=None,
    collection_row_id=None,
):
    doctype = "CN Reconciliation Exception"
    existing_name = frappe.db.get_value(
        doctype, {"period": period.name, "exception_key": exception_key}, "name",
    )
    if not existing_name:
        # Adopt pre-key records when possible; do not discard prior follow-up.
        legacy_filters = {
            "period": period.name, "source_import": ["is", "not set"],
        }
        if collection_row_id:
            legacy_filters["collection_row_id"] = collection_row_id
            legacy_filters["exception_type"] = ["in", sorted(EXCEPTION_STATES | {
                "Pendiente de detalle de empresa",
            })]
        elif source_row:
            legacy_filters.update({
                "source_row": source_row, "client_number": client_number,
                "loan_number": loan_number, "exception_type": exception_type,
                "exception_key": ["is", "not set"],
            })
        else:
            legacy_filters = None
        if legacy_filters:
            candidates = frappe.get_all(
                doctype, filters=legacy_filters,
                fields=["name", "status", "modified"],
                limit_page_length=1000,
            )
            if candidates:
                # Older installations keyed each classification separately.
                # Keep the case carrying the most follow-up, archive the rest.
                existing_name = max(
                    candidates,
                    key=lambda item: (
                        frappe.db.count("CN Exception Action", {"parent": item.name}),
                        item.status in {"Abierta", "En revision"},
                        str(item.modified or ""),
                    ),
                ).name
    if existing_name:
        exception = frappe.get_doc(doctype, existing_name)
        old_type = clean_text(exception.exception_type)
        type_changed = old_type != exception_type
        old_usd, old_nio = flt(exception.amount_usd), flt(exception.amount_nio)
        amount_changed = (
            abs(old_usd - flt(amount_usd)) > CASH_EPSILON
            or abs(old_nio - flt(amount_nio)) > CASH_EPSILON
        )
        if type_changed:
            exception.append("follow_up_actions", {
                "action_type": "Ajuste",
                "details": _(
                    "El detalle actualizado reclasificó la excepción de {0} a {1}."
                ).format(old_type, exception_type),
            })
        if exception.status == "Descartada" or (
            exception.status == "Resuelta" and (amount_changed or type_changed)
        ):
            previous_resolution = clean_text(exception.resolution)
            exception.append("follow_up_actions", {
                "action_type": "Ajuste",
                "details": _(
                    "Nueva importación vuelve a mostrar esta diferencia; se reabre. Resolución previa: {0}"
                ).format(previous_resolution or "—"),
            })
            exception.status = "Abierta"
            exception.resolution = ""
    else:
        exception = frappe.get_doc({"doctype": doctype, "status": "Abierta"})
    exception.update({
        "exception_type": exception_type,
        "exception_key": exception_key,
        "period": period.name,
        "employer": period.employer,
        "source_row": source_row,
        "client_number": client_number,
        "loan_number": loan_number,
        "amount_usd": amount_usd,
        "amount_nio": amount_nio,
        "collection_row_id": collection_row_id,
        "description": description,
    })
    exception.flags.skip_comment_reconciliation = True
    if existing_name:
        exception.save(ignore_permissions=True)
    else:
        exception.insert(ignore_permissions=True)
    return exception.name


def _append_note(existing, line):
    return "\n".join(part for part in (existing, line) if part)


def _deduction_equivalents(row, response):
    deducted_usd = flt(response.get("deducted_usd"))
    deducted_nio = flt(response.get("deducted_nio"))
    note = ""
    if (
        deducted_nio > 0
        and deducted_usd == 0
        and flt(row.expected_nio) > 0
        and flt(row.expected_usd) > 0
    ):
        deducted_usd = money_float(
            decimal_value(deducted_nio) * decimal_value(row.expected_usd)
            / decimal_value(row.expected_nio)
        )
        note = _(
            "El equivalente US$ se calculo con la relacion congelada del archivo de cobranza."
        )
    elif (
        deducted_usd > 0
        and deducted_nio == 0
        and flt(row.expected_usd) > 0
        and flt(row.expected_nio) > 0
    ):
        deducted_nio = money_float(
            decimal_value(deducted_usd) * decimal_value(row.expected_nio)
            / decimal_value(row.expected_usd)
        )
        note = _(
            "El equivalente C$ se calculo con la relacion congelada del archivo de cobranza."
        )
    return money_float(deducted_usd), money_float(deducted_nio), note


def _retire_obsolete_deduction_exceptions(period_name, seen_names):
    doctype = "CN Reconciliation Exception"
    names = set(frappe.get_all(
        doctype,
        filters={
            "period": period_name,
            "source_import": ["is", "not set"],
            "exception_key": ["like", "DED-%"],
            "status": ["in", ["Abierta", "En revision", "Resuelta"]],
        },
        pluck="name",
    ))
    # Older imports had no stable key. Adopt those still present above; retire
    # only unmatched system-generated rows, retaining their audit trail.
    legacy_names = frappe.get_all(
        doctype,
        filters={
            "period": period_name,
            "source_import": ["is", "not set"],
            "exception_key": ["is", "not set"],
            "exception_type": ["in", sorted(EXCEPTION_STATES | {
                "Pendiente de detalle de empresa", "Detalle de empresa sin coincidencia",
            })],
            "status": ["in", ["Abierta", "En revision", "Resuelta"]],
        },
        fields=["name", "collection_row_id", "source_row"],
    )
    names.update(
        item.name for item in legacy_names
        if item.collection_row_id or item.source_row
    )
    for name in names:
        if name in seen_names:
            continue
        exception = frappe.get_doc(doctype, name)
        if exception.status == "Resuelta":
            previous_resolution = clean_text(exception.resolution)
            exception.append("follow_up_actions", {
                "action_type": "Ajuste",
                "details": _(
                    "La diferencia resuelta dejó de aparecer en el detalle actualizado. "
                    "Resolución humana previa: {0}"
                ).format(previous_resolution or "—"),
            })
        exception.status = "Descartada"
        exception.resolution = _(
            "La diferencia ya no aparece en el detalle de empresa importado nuevamente."
        )
        exception.flags.skip_comment_reconciliation = True
        exception.save(ignore_permissions=True)
