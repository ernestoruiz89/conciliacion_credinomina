from __future__ import annotations

import hashlib
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

from credinomina_reconciliation.allocation import allocate_cash, can_document_surplus
from credinomina_reconciliation.cadence import unique_full_quincena_pair
from credinomina_reconciliation.deduction_recognition import recognition_reason
from credinomina_reconciliation.deposit_scoping import (
    deposit_scope,
    duplicate_deposit_key,
    resolved_deposit_employer,
)
from credinomina_reconciliation.employer_naming import employer_alias_index
from credinomina_reconciliation.historical import (
    blocked_historical_deposits,
    historical_balance,
    historical_scope_contains,
    historical_status,
    is_historical_date,
)
from credinomina_reconciliation.parsers import (
    SOURCE_ACCOUNTING,
    SOURCE_DEPOSITS,
    SOURCE_TRANSACTIONS,
    SourceFileError,
    canonical_identifier,
    clean_text,
    file_sha256,
    parse_source_file,
)
from credinomina_reconciliation.reconciliation import (
    AMOUNT_TOLERANCE,
    complementary_matches_collection,
    converted_amount,
    deposit_pair_result,
    documented_rate,
    duplicate_business_key,
    matching_exception_notes,
    narrow_deposit_candidates_by_date,
    same_amount,
    source_priority,
)
from credinomina_reconciliation.rounding import CASH_EPSILON, rounding_movements
from credinomina_reconciliation.remittance_detail import (
    detail_amount_usd,
    suggest_detail_targets,
)


class _RegisteredDeposit(dict):
    """Deposit entered in the app, shaped like an imported source row."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value

    def as_dict(self):
        return dict(self)


class CNSourceImport(Document):
    def validate(self):
        self._validate_historical_periods()
        if any(
            row.deposit_scope and (
                self.source_type != SOURCE_DEPOSITS or row.event_type != "Deposito"
            )
            for row in self.rows or []
        ):
            frappe.throw(_("El tratamiento del depósito solo corresponde a la pestaña Depósito."))
        self._validate_duplicate_file()
        self._validate_manual_rates()
        self.recalculate_summary()

    def _validate_historical_periods(self):
        if self.historical_period and self.source_type == "Detalle de depositos":
            frappe.throw(_("El período histórico se asigna a aplicaciones, no al archivo bancario."))
        if self.historical_backfill and self.source_type == "Detalle de depositos":
            frappe.throw(_("La carga histórica de aplicaciones no corresponde al archivo bancario."))
        if any(
            row.historical_period and row.event_type != "Aplicacion"
            for row in self.rows or []
        ):
            frappe.throw(_("Solo las filas de aplicación pueden tener período histórico."))
        if any(
            row.processing_route and row.event_type != "Aplicacion"
            for row in self.rows or []
        ):
            frappe.throw(_("La ruta de aplicación no corresponde a un depósito."))
        if any(
            row.processing_route == "Operativa" and row.historical_period
            for row in self.rows or []
        ):
            frappe.throw(_("Una aplicación marcada Operativa no puede tener período histórico."))
        if any(
            row.processing_route == "Operativa" and is_historical_date(row.event_date)
            for row in self.rows or []
        ):
            frappe.throw(_("Una aplicación fechada antes de septiembre de 2026 no puede usar la ruta Operativa."))
        selected = {self.historical_period} if self.historical_period else set()
        selected.update(
            row.historical_period for row in self.rows or []
            if row.event_type == "Aplicacion" and row.historical_period
        )
        periods = {
            period.name: period for period in frappe.get_all(
                "CN Reconciliation Period",
                filters={"name": ["in", list(selected)]},
                fields=[
                    "name", "reconciliation_mode", "historical_scope",
                    "historical_application_date", "historical_start_date",
                    "historical_end_date",
                ],
                limit_page_length=max(len(selected), 20),
            )
        } if selected else {}
        for name in selected:
            if name not in periods or periods[name].reconciliation_mode != "Historica":
                frappe.throw(_("{0} no es un período histórico.").format(name))
        for row in self.rows or []:
            if row.event_type != "Aplicacion" or row.processing_route == "Operativa":
                continue
            period_name = row.historical_period or self.historical_period
            if not period_name:
                continue
            period = periods[period_name]
            if not historical_scope_contains(
                period.historical_scope, row.event_date,
                period.historical_application_date, period.historical_start_date,
                period.historical_end_date,
            ):
                frappe.throw(_(
                    "La fecha de la aplicación en la fila {0} no pertenece al corte histórico {1}."
                ).format(row.idx, period_name))

    def _validate_manual_rates(self):
        for row in self.rows or []:
            rate = flt(row.manual_fx_rate)
            if rate < 0:
                frappe.throw(_("La fila {0} tiene un tipo de cambio negativo.").format(row.idx))
            if rate and not clean_text(row.manual_fx_evidence):
                frappe.throw(
                    _("Documente la fuente del tipo de cambio autorizado en la fila {0}.").format(row.idx)
                )
            if rate and row.fx_basis:
                frappe.throw(
                    _("La fila {0} ya tiene un tipo de cambio documentado en el archivo.").format(row.idx)
                )

    def _validate_duplicate_file(self):
        if not self.file_hash:
            return
        duplicate = frappe.db.get_value(
            self.doctype,
            {
                "name": ["!=", self.name or ""],
                "source_type": self.source_type,
                "file_hash": self.file_hash,
                "status": ["!=", "Fallido"],
            },
            "name",
        )
        if duplicate:
            frappe.throw(_("Este archivo ya fue importado en {0}.").format(duplicate))

    def recalculate_summary(self):
        rows = list(self.rows or [])
        self.row_count = len(rows)
        self.matched_count = sum(
            row.match_status == "Conciliado"
            and (
                row.event_type != "Deposito"
                or flt(row.unallocated_usd) <= CASH_EPSILON
            )
            and (
                row.event_type != "Aplicacion"
                or row.deposit_match_status == "Remesa conciliada"
            )
            for row in rows
        )
        self.exception_count = sum(
            row.match_status in {"Ambiguo", "Sin coincidencia"}
            or (
                row.event_type == "Deposito"
                and row.effective
                and flt(row.unallocated_usd) > CASH_EPSILON
            )
            or (
                row.event_type == "Aplicacion"
                and row.effective
                and row.deposit_match_status != "Remesa conciliada"
            )
            for row in rows
        )
        self.ignored_count = sum(row.match_status == "Ignorado" for row in rows)
        self.total_usd = sum(flt(row.amount_usd) for row in rows if row.effective)
        self.total_nio = sum(flt(row.amount_nio) for row in rows if row.effective)


def _attached_file(document):
    if not document.source_file:
        frappe.throw(_("Adjunte el archivo de origen."))
    file_doc = frappe.get_doc("File", {"file_url": document.source_file})
    if (
        file_doc.attached_to_doctype != document.doctype
        or file_doc.attached_to_name != document.name
    ):
        frappe.throw(_("El archivo debe estar adjunto a esta importacion."))
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    return file_doc, content


@frappe.whitelist(methods=["POST"])
def import_source_file(import_name: str):
    document = frappe.get_doc("CN Source Import", import_name)
    document.check_permission("write")
    file_doc, content = _attached_file(document)
    try:
        parsed = parse_source_file(document.source_type, file_doc.file_name, content)
    except SourceFileError as exc:
        document.status = "Fallido"
        document.notes = str(exc)
        document.save()
        frappe.throw(str(exc), title=_("No se pudo importar la fuente"))

    existing_settings = defaultdict(list)
    for row in document.rows or []:
        if row.source_key:
            existing_settings[row.source_key].append(
                (
                    row.name, row.manual_fx_rate,
                    row.manual_fx_evidence, row.historical_period,
                    row.processing_route, row.deposit_scope,
                )
            )
    document.file_hash = file_sha256(content)
    document.set("rows", [])
    for record in parsed:
        previous = existing_settings[record["source_key"]]
        preserved_name, manual_rate, manual_evidence, prior_period, prior_route, prior_scope = (
            previous.pop(0) if previous else (None, 0, "", "", "", "")
        )
        document.append(
            "rows",
            {
                **record,
                **({"name": preserved_name} if preserved_name else {}),
                "manual_fx_rate": manual_rate,
                "manual_fx_evidence": manual_evidence,
                "processing_route": prior_route,
                "deposit_scope": prior_scope if record["event_type"] == "Deposito" else "",
                "historical_period": (
                    prior_period or document.historical_period
                    if record["event_type"] == "Aplicacion" else ""
                ),
                "effective": 1,
                "match_status": "Pendiente",
                "deposit_match_status": "Pendiente",
            },
        )
    document.imported_on = now_datetime()
    document.imported_by = frappe.session.user
    document.status = "Importado"
    document.notes = _("Se importaron {0} filas de {1}.").format(
        len(parsed), file_doc.file_name
    )
    document.save()
    result = reconcile_all_sources()
    result["import_name"] = document.name
    return result


@frappe.whitelist(methods=["POST"])
def reconcile_all_sources():
    if not frappe.has_permission("CN Source Import", "write"):
        frappe.throw(_("No tiene permiso para conciliar importaciones."))

    import_names = frappe.get_all(
        "CN Source Import",
        filters={"status": ["in", ["Importado", "Importado con excepciones"]]},
        order_by="creation asc",
        pluck="name",
    )
    imports = [frappe.get_doc("CN Source Import", name) for name in import_names]
    all_rows = []
    for document in imports:
        for row in document.rows:
            row._source_type = document.source_type
            row._source_import = document.name
            row._historical_backfill = (
                row.processing_route == "Historica"
                or (
                    row.processing_route != "Operativa"
                    and bool(document.historical_backfill or document.historical_period)
                )
            )
            row.effective = 1
            row.match_status = "Pendiente"
            row.match_reason = ""
            row.collection_period = ""
            row.collection_row_id = ""
            row.application_allocation_detail = "[]"
            row.deposit_match_status = "Pendiente"
            row.deposit_match_reason = ""
            row.fx_variance_usd = 0
            row.rounding_adjustment_usd = 0
            row.complementary_usd = 0
            row.rounding_movement_detail = "[]"
            row.allocated_usd = 0
            row.unallocated_usd = 0
            row.justified_surplus_usd = 0
            row.unclassified_usd = 0
            row.allocation_reason = ""
            row.allocation_detail = "[]"
            row.inherited_exception_comment = ""
            row.historical_remitted_usd = 0
            row.historical_balance_usd = 0
            row.historical_detail = "[]"
            row.historical_application_id = (
                row.name if row.event_type == "Aplicacion" and row.historical_period
                else ""
            )
            if row.event_type == "Aplicacion" and not row.historical_period:
                row.historical_period = document.historical_period or ""
                if row.historical_period:
                    row.historical_application_id = row.name
            all_rows.append(row)

    _apply_source_precedence(all_rows)
    periods = _load_open_periods()
    collection_rows = [row for period in periods for row in period.collection_rows]
    complementary_items = frappe.get_all(
        "CN Complementary Item",
        filters={"docstatus": 1},
        fields=[
            "name", "reference", "amount_usd", "employer", "period",
            "client_number", "loan_number", "installment_number",
        ],
    )
    complementary_by_target = _allocate_complementary_items(
        complementary_items, periods
    )
    manual_allocations = frappe.get_all(
        "CN Remittance Allocation",
        filters={"docstatus": 1},
        fields=[
            "name", "deposit_reference", "deposit_voucher", "period",
            "row_key", "complementary_item", "historical_application",
            "amount_usd", "result", "employer", "deposit_date",
            "deposit_currency", "deposit_amount", "fx_rate", "fx_evidence",
            "allocated_usd", "unallocated_usd",
            "support_file", "detail_file", "detail_source_file", "detail_hash", "detail_period",
            "detail_status", "detail_total_usd", "detail_count",
        ],
        order_by="creation asc",
    )
    surplus_items = frappe.get_all(
        "CN Deposit Surplus",
        filters={"docstatus": 1},
        fields=[
            "name", "period", "registered_deposit", "deposit_reference", "deposit_voucher",
            "amount_usd", "result",
        ],
        order_by="creation asc",
    )
    legacy_pairs = _scope_deposit_pairs(all_rows, _match_deposits(all_rows))
    deposit_pairs, registered_ids = _registered_deposit_pairs(
        all_rows, manual_allocations, legacy_pairs
    )
    _refresh_recognition_evidence(periods, deposit_pairs)
    _match_applications(
        all_rows, collection_rows, deposit_pairs, complementary_by_target, periods
    )
    allocation = _distribute_deposits(
        periods, all_rows, deposit_pairs, complementary_items,
        complementary_by_target, manual_allocations, registered_ids,
    )
    _sync_rounding_movements(allocation["rounding_movements"], allocation, all_rows)
    _classify_surplus(allocation, surplus_items)
    _rebuild_period_balances(
        [period for period in periods if period.reconciliation_mode != "Historica"],
        all_rows, deposit_pairs, allocation,
    )
    _rebuild_historical_balances(periods, all_rows, allocation)
    _sync_registered_deposit_detail(allocation)

    for document in imports:
        document.recalculate_summary()
        document.status = (
            "Importado con excepciones" if document.exception_count else "Importado"
        )
        document.save(ignore_permissions=True)

    return {
        "imports": len(imports),
        "rows": len(all_rows),
        "matched": sum(
            row.match_status == "Conciliado"
            and (
                row.event_type != "Deposito"
                or flt(row.unallocated_usd) <= CASH_EPSILON
            )
            and (
                row.event_type != "Aplicacion"
                or row.deposit_match_status == "Remesa conciliada"
            )
            for row in all_rows
        ),
        "exceptions": sum(
            row.match_status in {"Ambiguo", "Sin coincidencia"}
            or (
                row.event_type == "Deposito"
                and row.effective
                and flt(row.unallocated_usd) > CASH_EPSILON
            )
            or (
                row.event_type == "Aplicacion"
                and row.effective
                and row.deposit_match_status != "Remesa conciliada"
            )
            for row in all_rows
        ),
        "ignored": sum(row.match_status == "Ignorado" for row in all_rows),
    }


def _apply_source_precedence(rows):
    application_rows = [row for row in rows if row.event_type == "Aplicacion"]
    application_rows.sort(
        key=lambda row: (
            source_priority(row._source_type, row.event_type),
            row._source_import,
            row.idx,
        )
    )
    claimed = {}
    primary_by_reference = defaultdict(list)
    for row in application_rows:
        if row._source_type != SOURCE_ACCOUNTING:
            continue
        if row.reference:
            primary_by_reference[
                (canonical_identifier(row.loan_number), clean_text(row.reference))
            ].append(row)
    for row in rows:
        if row.event_type == "Ajuste":
            row.effective = 0
            row.match_status = "Ignorado"
            row.match_reason = _(
                "Las dispensas y ajustes no son pagos en efectivo y se revisan por separado."
            )
    for row in application_rows:
        if (
            row._source_type == SOURCE_TRANSACTIONS
            and row.reference
            and any(
                same_amount(primary.amount, row.amount)
                and primary.currency == row.currency
                and str(primary.event_date or "")[:10] == str(row.event_date or "")[:10]
                for primary in primary_by_reference[
                    (canonical_identifier(row.loan_number), clean_text(row.reference))
                ]
            )
        ):
            row.effective = 0
            row.match_status = "Ignorado"
            row.match_reason = _(
                "Movimientos contables ya contiene esta aplicacion; Transacciones se usa solo como respaldo."
            )
            continue
        key = duplicate_business_key(row.as_dict())
        preferred = claimed.get(key)
        if not preferred:
            claimed[key] = row
            continue
        row.effective = 0
        row.match_status = "Ignorado"
        row.match_reason = _("La fuente principal ya contiene esta aplicacion.")

    # Monthly Depósito workbooks can retain unresolved rows from earlier months.
    # Keep the first identical bank entry, but not a second copy from a later file.
    first_deposit_by_key = {}
    for row in rows:
        if (
            row._source_type != SOURCE_DEPOSITS
            or row.event_type != "Deposito"
            or not row.effective
        ):
            continue
        key = duplicate_deposit_key(row.as_dict())
        if key is None:
            continue
        first = first_deposit_by_key.get(key)
        if first and first._source_import != row._source_import:
            row.effective = 0
            row.match_status = "Ignorado"
            row.match_reason = _(
                "El mismo depósito ya fue importado desde {0}."
            ).format(first._source_import)
        elif first is None:
            first_deposit_by_key[key] = row


def _load_open_periods():
    names = frappe.get_all(
        "CN Reconciliation Period",
        order_by="payroll_month asc",
        pluck="name",
    )
    return [frappe.get_doc("CN Reconciliation Period", name) for name in names]


def _refresh_recognition_evidence(periods, deposit_pairs):
    """Withdraw inferred payroll detail if its underlying deposit ceases to match."""
    pairs_by_account = {account.name: (account, bank) for account, bank in deposit_pairs}
    for period in periods:
        if period.deduction_basis != "Depósito coincidente":
            continue
        pair = pairs_by_account.get(period.deduction_recognition_deposit)
        reason = ""
        if not pair:
            reason = _("El depósito de respaldo ya no está conciliado entre banco y contabilidad.")
        else:
            account, bank = pair
            rows = [
                {**row.as_dict(), "deduction_status": "Pendiente de detalle"}
                for row in period.collection_rows
            ]
            account_check = {**account.as_dict(), "allocated_usd": 0, "justified_surplus_usd": 0}
            bank_check = {**bank.as_dict(), "allocated_usd": 0, "justified_surplus_usd": 0}
            reason = recognition_reason(rows, account_check, bank_check)
            if not reason and clean_text(account.reference) != clean_text(
                period.deduction_recognition_reference
            ):
                reason = _("Cambió la referencia del depósito de respaldo.")
            if not reason and not (account.event_date or bank.event_date):
                reason = _("El depósito de respaldo ya no tiene fecha verificable.")
            if (
                not reason and period.cutoff_date
                and getdate(account.event_date or bank.event_date) < getdate(period.cutoff_date)
            ):
                reason = _("El depósito de respaldo ahora precede el cierre de la cobranza.")
        if not reason:
            continue
        if period.status == "Cerrado":
            frappe.throw(
                _("La evidencia del período cerrado {0} dejó de ser válida: {1}").format(
                    period.name, reason
                )
            )
        was_inferred = False
        for row in period.collection_rows:
            if row.deduction_status != "Inferida por depósito":
                continue
            was_inferred = True
            row.deducted_usd = 0
            row.deducted_nio = 0
            row.deduction_currency = ""
            row.deduction_status = "Pendiente de detalle"
            row.deduction_evidence_date = None
            row.deduction_match_note = _("Reconocimiento suspendido: {0}").format(reason)
        if was_inferred:
            period.status = "Cobranza cargada"
            period.notes = "\n".join(
                part for part in (
                    period.notes,
                    _("Reconocimiento por depósito suspendido: {0}").format(reason),
                ) if part
            )


def _deducted_amount(row, currency):
    if currency == "USD":
        direct = flt(row.deducted_usd)
        if direct > AMOUNT_TOLERANCE:
            return direct
        if flt(row.deducted_nio) and flt(row.expected_nio) and flt(row.expected_usd):
            return flt(row.expected_usd) * flt(row.deducted_nio) / flt(row.expected_nio)
    if currency == "NIO":
        direct = flt(row.deducted_nio)
        if direct > AMOUNT_TOLERANCE:
            return direct
        if flt(row.deducted_usd) and flt(row.expected_usd) and flt(row.expected_nio):
            return flt(row.expected_nio) * flt(row.deducted_usd) / flt(row.expected_usd)
    return 0


def _allocate_complementary_items(items, periods):
    """An optional customer allocation must identify exactly one collection row."""
    allocations = defaultdict(list)
    for item in items:
        if not item.loan_number:
            continue
        candidates = [
            row
            for period in periods
            for row in period.collection_rows
            if complementary_matches_collection(
                item,
                {**row.as_dict(), "employer": period.employer},
                item.reference,
            )
        ]
        if len(candidates) == 1:
            allocations[(candidates[0].name, clean_text(item.reference))].append(item)
    return allocations


def _application_allocations(source):
    """Read the auditable USD split, including pre-upgrade single-row links."""
    detail = json.loads(source.application_allocation_detail or "[]")
    if detail:
        return detail
    if source.collection_row_id:
        return [{"collection_row_id": source.collection_row_id,
                 "amount_usd": flt(source.amount)}]
    return []


def _match_applications(
    source_rows, collection_rows, deposit_pairs, complementary_by_target, periods
):
    historical_periods = {
        period.name: period for period in periods
        if period.reconciliation_mode == "Historica"
    }
    period_by_name = {period.name: period for period in periods}
    pairs_by_reference = defaultdict(list)
    for account, bank in deposit_pairs:
        pairs_by_reference[clean_text(account.reference)].append((account, bank))
    applied_by_target = defaultdict(float)
    for source in source_rows:
        if source.event_type != "Aplicacion" or not source.effective:
            continue
        if source.currency != "USD":
            source.match_status = "Sin coincidencia"
            source.match_reason = _(
                "Las aplicaciones del core deben expresarse en US$. Revise la moneda de esta fila."
            )
            continue
        if source.historical_period:
            period = historical_periods.get(source.historical_period)
            if not period:
                source.match_status = "Sin coincidencia"
                source.match_reason = _("Asigne un período histórico válido a la aplicación.")
                continue
            if not historical_scope_contains(
                period.historical_scope, source.event_date,
                period.historical_application_date, period.historical_start_date,
                period.historical_end_date,
            ):
                source.match_status = "Sin coincidencia"
                source.match_reason = _("La fecha de aplicación no pertenece al corte histórico elegido.")
                continue
            source.match_status = "Conciliado"
            source.match_reason = _(
                "Aplicación histórica asignada a {0}; se conciliará directamente con depósitos."
            ).format(period.name)
            source.collection_period = period.name
            continue
        if source._historical_backfill or is_historical_date(source.event_date):
            source.match_status = "Sin coincidencia"
            source.match_reason = _(
                "Aplicación del histórico: asigne el período de empresa y mes; no se compara con cobranza operativa."
            )
            continue
        reference_pairs = pairs_by_reference.get(clean_text(source.reference)) or []
        payment_rate = None
        if len(reference_pairs) == 1:
            account, bank = reference_pairs[0]
            payment_rate = documented_rate(account.as_dict()) or documented_rate(
                bank.as_dict()
            )
        candidates = []
        pair_candidates = []
        for target in collection_rows:
            if canonical_identifier(target.loan_number) != canonical_identifier(
                source.loan_number
            ):
                continue
            if source.installment_number and canonical_identifier(
                target.installment_number
            ) != canonical_identifier(source.installment_number):
                continue
            if target.deduction_status == "No deducido":
                continue
            if source.client_number and canonical_identifier(
                target.client_number
            ) != canonical_identifier(source.client_number):
                continue
            if source.national_id and clean_text(
                target.national_id
            ).casefold() != clean_text(source.national_id).casefold():
                continue
            if target.application_reference and clean_text(
                target.application_reference
            ) != clean_text(source.reference):
                continue
            complementary = sum(
                flt(item.amount_usd)
                for item in complementary_by_target.get(
                    (target.name, clean_text(source.reference)), []
                )
            )
            detail_pending = target.deduction_status in {"", "Pendiente de detalle"}
            deducted_usd = _deducted_amount(target, "USD")
            direct_capacity = max(
                (flt(target.expected_usd) if detail_pending else deducted_usd)
                - complementary,
                0,
            )
            converted_capacity = (
                max(flt(target.deducted_nio) / payment_rate - complementary, 0)
                if target.deduction_currency == "NIO" and payment_rate
                else 0
            )
            converted_match = (
                target.deduction_currency == "NIO"
                and payment_rate is not None
                and converted_capacity > direct_capacity + AMOUNT_TOLERANCE
            )
            available = max(direct_capacity, converted_capacity) - applied_by_target[target.name]
            if available > AMOUNT_TOLERANCE:
                target_period = period_by_name.get(target.parent)
                if target_period and target_period.reconciliation_mode != "Historica":
                    pair_candidates.append({
                        "target": target,
                        "available_usd": round(available, 4),
                        "cycle": target_period.collection_cycle,
                        "employer": target_period.employer,
                        "month": str(target_period.payroll_month)[:7],
                        "detail_pending": detail_pending,
                        "converted_match": converted_match,
                    })
            if flt(source.amount) > available + AMOUNT_TOLERANCE:
                continue
            exact = same_amount(flt(source.amount), available)
            candidates.append((target, converted_match, exact, detail_pending))
        confirmed_candidates = [candidate for candidate in candidates if not candidate[3]]
        if confirmed_candidates:
            candidates = confirmed_candidates
        exact_candidates = [candidate for candidate in candidates if candidate[2]]
        if confirmed_candidates and len(candidates) > 1 and len(exact_candidates) == 1:
            candidates = exact_candidates
        if len(candidates) == 1:
            target, converted_match, _exact, detail_pending = candidates[0]
            applied_by_target[target.name] += flt(source.amount)
            source.match_status = "Conciliado"
            if converted_match:
                source.match_reason = _(
                    "Aplicacion parcial o total en US$ enlazada a la deduccion en C$ con tasa documentada de {0} C$ por US$."
                ).format(round(payment_rate, 8))
            elif detail_pending:
                source.match_reason = _(
                    "Aplicacion enlazada de forma unica a la cobranza; falta confirmar la deduccion de la empresa."
                )
            else:
                source.match_reason = _(
                    "Aplicacion parcial o total enlazada por credito y referencia cuando fue informada; no excede la deduccion disponible."
                )
            source.collection_period = target.parent
            source.collection_row_id = target.name
            source.application_allocation_detail = json.dumps([{
                "collection_row_id": target.name,
                "period": target.parent,
                "amount_usd": round(flt(source.amount), 4),
            }], ensure_ascii=False)
        elif not candidates and (
            pair := unique_full_quincena_pair(pair_candidates, flt(source.amount))
        ):
            detail = []
            for candidate in pair:
                target = candidate["target"]
                amount = candidate["available_usd"]
                applied_by_target[target.name] += amount
                detail.append({
                    "collection_row_id": target.name,
                    "period": target.parent,
                    "amount_usd": amount,
                })
            source.match_status = "Conciliado"
            source.match_reason = _(
                "Una aplicacion del core cubre las dos quincenas del mismo credito, empresa y mes; reparto completo por saldos disponibles."
            )
            if any(candidate["detail_pending"] for candidate in pair):
                source.match_reason += " " + _(
                    "Falta confirmar la deduccion de la empresa."
                )
            if any(candidate["converted_match"] for candidate in pair):
                source.match_reason += " " + _(
                    "Conversion con tasa documentada de {0} C$ por US$."
                ).format(round(payment_rate, 8))
            source.collection_period = detail[0]["period"]
            source.application_allocation_detail = json.dumps(detail, ensure_ascii=False)
        elif len(candidates) > 1:
            source.match_status = "Ambiguo"
            source.match_reason = _(
                "Mas de una cuota podria recibir esta aplicacion parcial."
            )
        else:
            source.match_status = "Sin coincidencia"
            source.match_reason = _(
                "No existe una cobranza identificada con capacidad disponible para este credito e importe."
            )


def _match_deposits(rows):
    accounting = defaultdict(list)
    bank_detail = defaultdict(list)
    for row in rows:
        if row.event_type != "Deposito" or not row.effective:
            continue
        target = accounting if row._source_type == SOURCE_ACCOUNTING else bank_detail
        target[clean_text(row.reference)].append(row)

    paired = []
    for reference in set(accounting) | set(bank_detail):
        left = accounting.get(reference, [])
        right = bank_detail.get(reference, [])
        if not reference:
            for row in left + right:
                row.match_status = "Sin coincidencia"
                row.match_reason = _("El deposito no tiene referencia bancaria.")
            continue

        matches_by_left = {
            id(account): narrow_deposit_candidates_by_date(
                account.as_dict(), [
                    bank for bank in right
                    if deposit_pair_result(account.as_dict(), bank.as_dict())[0]
                ],
            )
            for account in left
        }
        matches_by_right = {
            id(bank): narrow_deposit_candidates_by_date(
                bank.as_dict(), [
                    account for account in left
                    if deposit_pair_result(account.as_dict(), bank.as_dict())[0]
                ],
            )
            for bank in right
        }
        paired_ids = set()
        for account in left:
            candidates = matches_by_left[id(account)]
            if len(candidates) != 1:
                continue
            bank = candidates[0]
            if len(matches_by_right[id(bank)]) != 1:
                continue
            _, reason = deposit_pair_result(account.as_dict(), bank.as_dict())
            for row in (account, bank):
                row.match_status = "Conciliado"
                row.match_reason = _(reason)
                paired_ids.add(id(row))
            paired.append((account, bank))

        for row, alternatives, opposite in (
            *((account, matches_by_left[id(account)], right) for account in left),
            *((bank, matches_by_right[id(bank)], left) for bank in right),
        ):
            if id(row) in paired_ids:
                continue
            if alternatives:
                row.match_status = "Ambiguo"
                row.match_reason = _("Hay mas de un deposito compatible con esta referencia e importe.")
            else:
                row.match_status = "Sin coincidencia"
                if opposite:
                    _, reason = deposit_pair_result(
                        row.as_dict(), opposite[0].as_dict()
                    )
                    row.match_reason = _(reason)
                else:
                    row.match_reason = _(
                        "Falta el deposito equivalente en contabilidad o en el detalle bancario."
                    )
    return paired


def _registered_deposit_pairs(rows, allocations, legacy_pairs):
    """Use manually registered deposits as cash evidence; keep unmatched legacy pairs."""
    registered = [item for item in allocations if item.deposit_date]
    if not registered:
        return legacy_pairs, {}
    accounting = [
        row for row in rows
        if row.event_type == "Deposito" and row.effective
        and row._source_type == SOURCE_ACCOUNTING
    ]
    used_account_ids = set()
    pairs = []
    deposit_ids = {}
    for item in registered:
        registered_row = _RegisteredDeposit(
            name=item.name,
            reference=clean_text(item.deposit_reference),
            voucher=clean_text(item.deposit_voucher),
            event_date=item.deposit_date,
            currency=item.deposit_currency,
            amount=flt(item.deposit_amount),
            equivalent_currency="USD",
            equivalent_amount=flt(item.amount_usd),
            fx_basis=clean_text(item.fx_evidence) if item.deposit_currency == "NIO" else "Moneda original USD",
            fx_rate=flt(item.fx_rate),
            manual_fx_rate=flt(item.fx_rate),
            manual_fx_evidence=clean_text(item.fx_evidence),
            employer_text=item.employer,
            effective=1,
            allocated_usd=0,
            unallocated_usd=0,
            justified_surplus_usd=0,
            unclassified_usd=0,
            allocation_reason="",
            allocation_detail="[]",
        )
        candidates = [
            account for account in accounting
            if account.name not in used_account_ids
            and (not item.deposit_voucher or clean_text(account.voucher) == clean_text(item.deposit_voucher))
            and deposit_pair_result(account.as_dict(), registered_row.as_dict())[0]
        ]
        candidates = narrow_deposit_candidates_by_date(registered_row, candidates)
        if len(candidates) == 1:
            account = candidates[0]
            used_account_ids.add(account.name)
            account.match_status = "Conciliado"
            account.match_reason = _("Depósito comprobado con registro y soporte adjunto.")
            pairs.append((registered_row, account))
            deposit_ids[item.name] = item.name
        else:
            # A real deposit may arrive after the core application and need not
            # have an imported accounting deposit row at registration time.
            pairs.append((registered_row, registered_row))
            deposit_ids[item.name] = item.name
    for account, bank in legacy_pairs:
        if account.name in used_account_ids:
            bank.match_status = "Ignorado"
            bank.match_reason = _("Sustituido por depósito registrado con soporte adjunto.")
            continue
        pairs.append((account, bank))
    return pairs, deposit_ids


def _scope_deposit_pairs(rows, paired):
    """Exclude clearly unrelated bank rows, retaining uncertain ones for review."""
    known_employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    aliases, ambiguous = employer_alias_index(known_employers)
    account_by_bank = {id(bank): account for account, bank in paired}
    excluded_banks = set()
    for bank in rows:
        if (
            bank._source_type != SOURCE_DEPOSITS
            or bank.event_type != "Deposito"
            or not bank.effective
        ):
            continue
        account = account_by_bank.get(id(bank))
        decision = deposit_scope(
            bank.as_dict(), account.as_dict() if account else None, aliases, ambiguous
        )
        if decision != "Excluir":
            continue
        excluded_banks.add(id(bank))
        reason = _(
            "Fuera de la conciliación de convenios según CLIENTE. "
            "Si corresponde a una empresa, seleccione Conciliar y vuelva a reconciliar."
        )
        for source in (bank, account):
            if source:
                source.effective = 0
                source.match_status = "Ignorado"
                source.match_reason = reason
    return [(account, bank) for account, bank in paired if id(bank) not in excluded_banks]


def _prepare_remittance_details(
    remittances, registered_ids, deposits, claims, prior_instructions,
    tolerance_by_employer,
):
    """Reserve a deposit for its client detail instead of guessing a split."""
    by_deposit = {deposit["id"]: deposit for deposit in deposits}
    names = [item.name for item in remittances if item.deposit_date]
    detail_rows = frappe.get_all(
        "CN Remittance Detail",
        filters={"parent": ["in", names]},
        fields=[
            "name", "parent", "source_row", "row_key", "client_number",
            "national_id", "loan_number", "installment_number",
            "application_reference", "deducted_usd", "deducted_nio",
            "amount_usd", "match_status", "match_reason", "matched_targets",
        ],
        order_by="parent asc, idx asc", limit_page_length=100000,
    ) if names else []
    rows_by_parent = defaultdict(list)
    for row in detail_rows:
        rows_by_parent[row.parent].append(row)
    reserved = {
        (entry["deposit_id"], entry["claim_id"])
        for entry in prior_instructions
    }
    instructions = []
    blocked = set()
    rounding_eligible = set()
    contexts = {}
    for item in remittances:
        if not item.deposit_date:
            continue
        deposit_id = registered_ids.get(item.name)
        if deposit_id not in by_deposit:
            continue
        deposit = by_deposit[deposit_id]
        rows = rows_by_parent[item.name]
        attached_detail = bool(item.detail_file or item.detail_hash)
        # An unimported deposit with several possible destinations is not
        # evidence that all of them were deducted by the company.
        if not attached_detail:
            reference = clean_text(deposit["reference"])
            potential = [
                claim for claim in claims
                if clean_text(claim.get("group")) == clean_text(item.employer)
                and reference in {clean_text(value) for value in claim.get("references", ())}
            ] if reference else []
            if len(potential) > 1:
                blocked.add(deposit_id)
                contexts[item.name] = {"status": "Detalle pendiente", "rows": []}
            continue
        blocked.add(deposit_id)
        if not item.detail_hash or not rows:
            contexts[item.name] = {"status": "Detalle pendiente", "rows": []}
            continue
        if item.detail_source_file != (item.detail_file or item.support_file):
            contexts[item.name] = {"status": "Importar detalle actualizado", "rows": []}
            continue
        plans = []
        total_usd = 0.0
        rate = flt(item.fx_rate) if clean_text(item.fx_evidence) else 0
        for row in rows:
            amount, explanation = detail_amount_usd(row, rate)
            total_usd = round(total_usd + amount, 4)
            plans.append({
                "row": row, "amount_usd": amount, "explanation": explanation,
                "targets": [], "status": "", "reason": "",
            })
        excessive = total_usd > flt(deposit["amount_usd"]) + CASH_EPSILON
        one_to_one_candidate = (
            len([plan for plan in plans if plan["amount_usd"] > CASH_EPSILON]) == 1
            and abs(total_usd - flt(deposit["amount_usd"])) <= CASH_EPSILON
            and deposit.get("currency") == "USD"
            and deposit.get("bank_currency") == "USD"
            and not any(entry["deposit_id"] == deposit_id for entry in prior_instructions)
        )
        for plan in plans:
            amount = plan["amount_usd"]
            if not amount:
                plan["status"] = (
                    "No deducido" if plan["explanation"] == "No deducido"
                    else "Revisar"
                )
                plan["reason"] = plan["explanation"]
                continue
            if excessive:
                plan["status"] = "Revisar"
                plan["reason"] = "La suma del detalle supera el depósito"
                continue
            targets, reason = suggest_detail_targets(
                plan["row"], claims, amount, item.employer,
                item.detail_period or "",
                tolerance_by_employer.get(item.employer, 0) if one_to_one_candidate else 0,
            )
            if not targets:
                plan["status"] = "Revisar"
                plan["reason"] = reason
                continue
            if any((deposit_id, target["claim_id"]) in reserved for target in targets):
                plan["status"] = "Revisar"
                plan["reason"] = "Destino ya cubierto por una distribución manual o depósito coincidente"
                continue
            plan["targets"] = targets
            plan["reason"] = reason + "; " + plan["explanation"]
            for target in targets:
                instruction_id = "D:" + plan["row"].name + ":" + target["claim_id"]
                instructions.append({
                    "id": instruction_id, "deposit_id": deposit_id,
                    "claim_id": target["claim_id"],
                    "amount_usd": target["amount_usd"],
                })
                reserved.add((deposit_id, target["claim_id"]))
        contexts[item.name] = {
            "status": "Detalle supera depósito" if excessive else "",
            "rows": plans, "total_usd": total_usd,
            "deposit_usd": flt(deposit["amount_usd"]),
        }
        if one_to_one_candidate and sum(
            bool(plan["targets"]) for plan in plans
        ) == 1:
            rounding_eligible.add(deposit_id)
    return {
        "instructions": instructions, "blocked_deposits": blocked,
        "contexts": contexts, "rounding_eligible": rounding_eligible,
    }


def _sync_remittance_details(context, allocation):
    statuses = {}
    for parent, state in context["contexts"].items():
        if state["rows"]:
            for plan in state["rows"]:
                targets = plan["targets"]
                if targets:
                    results = [
                        allocation["instruction_results"].get(
                            "D:" + plan["row"].name + ":" + target["claim_id"],
                            "Pendiente",
                        )
                        for target in targets
                    ]
                    plan["status"] = (
                        "Conciliada" if all(value == "Aplicada" for value in results)
                        else "Revisar"
                    )
                    if plan["status"] == "Revisar":
                        plan["reason"] += "; " + ", ".join(results)
                frappe.db.set_value(
                    "CN Remittance Detail", plan["row"].name,
                    {
                        "amount_usd": plan["amount_usd"],
                        "match_status": plan["status"],
                        "match_reason": plan["reason"],
                        "matched_targets": json.dumps(targets, ensure_ascii=False),
                    },
                    update_modified=False,
                )
            if not state["status"]:
                if any(plan["status"] != "Conciliada" for plan in state["rows"]):
                    state["status"] = "Revisar filas"
                elif state["total_usd"] < state["deposit_usd"] - CASH_EPSILON:
                    state["status"] = "Parcial; saldo sin detalle"
                else:
                    state["status"] = "Conciliado"
        statuses[parent] = state["status"]
        changes = {"detail_status": state["status"]}
        if "total_usd" in state:
            changes["detail_total_usd"] = state["total_usd"]
            changes["detail_count"] = len(state["rows"])
        frappe.db.set_value(
            "CN Remittance Allocation", parent, changes, update_modified=False,
        )
    return statuses


def _distribute_deposits(
    periods, source_rows, deposit_pairs, complementary_items,
    complementary_by_target, manual_allocations, registered_ids,
):
    rows_by_name = {
        row.name: row for period in periods for row in period.collection_rows
    }
    employer_by_period = {period.name: period.employer for period in periods}
    historical_applications = {
        row.name: row for row in source_rows
        if row.event_type == "Aplicacion" and row.effective
        and row.historical_period and row.match_status == "Conciliado"
    }
    known_employers = frappe.get_all(
        "CN Employer",
        fields=["name", "employer_name", "employer_code", "rounding_tolerance_usd"],
        limit_page_length=100000,
    )
    tolerance_by_employer = {
        employer.name: flt(employer.rounding_tolerance_usd, 4)
        for employer in known_employers
    }
    employer_by_label, ambiguous_labels = employer_alias_index(known_employers)
    row_by_key = {
        (period.name, clean_text(row.row_key)): row.name
        for period in periods for row in period.collection_rows
    }
    complementary_target = {
        item.name: target_name
        for (target_name, _reference), items in complementary_by_target.items()
        for item in items
    }
    complementary_totals = defaultdict(float)
    for item in complementary_items:
        target_name = complementary_target.get(item.name)
        if target_name:
            complementary_totals[target_name] += flt(item.amount_usd)

    deposits = []
    deposit_meta = {}
    ambiguous_deposit_ids = set()
    unresolved_employer_ids = set()
    for account, bank in deposit_pairs:
        account_usd = converted_amount(account.as_dict(), "USD")
        bank_usd = converted_amount(bank.as_dict(), "USD")
        if account_usd is None and bank_usd is None:
            account.allocation_reason = bank.allocation_reason = _(
                "Falta tipo de cambio documentado para distribuir el deposito."
            )
            for source in (account, bank):
                source.match_status = "Sin coincidencia"
                source.match_reason = source.allocation_reason
            continue
        if (
            account_usd is not None and bank_usd is not None
            and not same_amount(account_usd, bank_usd)
        ):
            account.allocation_reason = bank.allocation_reason = _(
                "Los equivalentes US$ del banco y contabilidad difieren."
            )
            for source in (account, bank):
                source.match_status = "Sin coincidencia"
                source.match_reason = source.allocation_reason
            continue
        amount_usd = round(account_usd if account_usd is not None else bank_usd, 4)
        native_nio = (
            flt(account.amount) if account.currency == "NIO"
            else flt(bank.amount) if bank.currency == "NIO" else 0
        )
        employer, unresolved_employer = resolved_deposit_employer(
            account.as_dict(), bank.as_dict(), employer_by_label, ambiguous_labels
        )
        if unresolved_employer:
            ambiguous_deposit_ids.add(account.name)
            unresolved_employer_ids.add(account.name)
        deposits.append(
            {"id": account.name, "reference": clean_text(account.reference),
             "amount_usd": amount_usd,
             "currency": account.currency, "bank_currency": bank.currency,
             "bank_amount_usd": flt(bank.amount) if bank.currency == "USD" else 0,
             "group": employer}
        )
        deposit_meta[account.name] = {
            "account": account, "bank": bank,
            "amount_usd": amount_usd,
            "nio_per_usd": native_nio / amount_usd if amount_usd else 0,
        }

    references_by_row = defaultdict(set)
    hints_by_row = defaultdict(lambda: defaultdict(float))
    core_amount_by_row = defaultdict(float)
    application_ids_by_row = defaultdict(set)
    for source in source_rows:
        if (
            source.event_type == "Aplicacion" and source.effective
            and source.match_status == "Conciliado"
        ):
            reference = clean_text(source.reference)
            for link in _application_allocations(source):
                row_id = link["collection_row_id"]
                core_amount_by_row[row_id] += flt(link["amount_usd"])
                application_ids_by_row[row_id].add(source.name)
                if reference:
                    references_by_row[row_id].add(reference)
                    hints_by_row[row_id][reference] += flt(link["amount_usd"])

    fallback_identities = defaultdict(set)
    for source in source_rows:
        if source.event_type != "Aplicacion" or source._source_type != SOURCE_TRANSACTIONS:
            continue
        if not (source.client_number or source.national_id):
            continue
        key = (
            canonical_identifier(source.loan_number), clean_text(source.reference),
            round(flt(source.amount), 4), source.currency,
            str(source.event_date or "")[:10],
        )
        fallback_identities[key].add((
            clean_text(source.client_number), clean_text(source.national_id),
            clean_text(source.installment_number),
        ))

    claims = []
    for row in rows_by_name.values():
        deducted_usd = _deducted_amount(row, "USD")
        loan_amount = max(deducted_usd - complementary_totals[row.name], 0)
        if loan_amount <= CASH_EPSILON:
            continue
        references = set(references_by_row[row.name])
        if row.application_reference:
            references.add(clean_text(row.application_reference))
        claims.append(
            {"id": "C:" + row.name, "amount_usd": loan_amount,
             "kind": "C", "row_key": row.row_key,
             "client_number": row.client_number, "national_id": row.national_id,
             "loan_number": row.loan_number,
             "installment_number": row.installment_number,
             "references": references, "hints": dict(hints_by_row[row.name]),
             "group": employer_by_period.get(row.parent),
             "period": row.parent,
             "core_applied_usd": round(core_amount_by_row[row.name], 4),
             "application_ids": sorted(application_ids_by_row[row.name])}
        )
    for item in complementary_items:
        claims.append(
            {"id": "X:" + item.name, "amount_usd": flt(item.amount_usd),
             "kind": "X", "client_number": item.client_number,
             "loan_number": item.loan_number,
             "installment_number": item.installment_number,
             "references": [clean_text(item.reference)], "hints": {},
            "group": item.employer or employer_by_period.get(item.period),
             "period": item.period}
        )
    for application in historical_applications.values():
        identity_key = (
            canonical_identifier(application.loan_number),
            clean_text(application.reference), round(flt(application.amount), 4),
            application.currency, str(application.event_date or "")[:10],
        )
        identity_options = fallback_identities.get(identity_key, set())
        fallback_identity = next(iter(identity_options)) if len(identity_options) == 1 else ("", "", "")
        claims.append(
            {
                "id": "H:" + application.name,
                "amount_usd": flt(application.amount),
                "kind": "H", "client_number": application.client_number or fallback_identity[0],
                "national_id": application.national_id or fallback_identity[1],
                "loan_number": application.loan_number,
                "installment_number": application.installment_number or fallback_identity[2],
                "references": [clean_text(application.reference)],
                "hints": {},
                "group": employer_by_period.get(application.historical_period),
                "period": application.historical_period,
                "core_applied_usd": flt(application.amount),
                "application_ids": [application.name],
            }
        )

    deposits_by_reference = defaultdict(list)
    for deposit in deposits:
        deposits_by_reference[deposit["reference"]].append(deposit)
    claims_by_id = {claim["id"]: claim for claim in claims}
    instructions = []
    # A user-approved exact deposit is reserved for every row of its payroll
    # period. These instructions precede optional manual cash allocations.
    for period in periods:
        if (
            period.reconciliation_mode == "Historica"
            or period.deduction_basis != "Depósito coincidente"
            or not period.deduction_recognition_deposit
            or period.deduction_recognition_deposit not in deposit_meta
            or not period.collection_rows
            or any(row.deduction_status != "Inferida por depósito" for row in period.collection_rows)
        ):
            continue
        deposit_id = period.deduction_recognition_deposit
        period_rows = {row.name for row in period.collection_rows}
        for row in period.collection_rows:
            claim_id = "C:" + row.name
            if claim_id in claims_by_id:
                instructions.append({
                    "id": "R:" + period.name + ":" + row.name,
                    "deposit_id": deposit_id,
                    "claim_id": claim_id,
                    "amount_usd": claims_by_id[claim_id]["amount_usd"],
                })
        for item in complementary_items:
            if complementary_target.get(item.name) in period_rows:
                instructions.append({
                    "id": "R:" + period.name + ":X:" + item.name,
                    "deposit_id": deposit_id,
                    "claim_id": "X:" + item.name,
                    "amount_usd": flt(item.amount_usd),
                })
    manual_results = {}
    manually_ambiguous_deposits = (
        blocked_historical_deposits(claims, deposits) | ambiguous_deposit_ids
    )
    registered_names = [item.name for item in manual_allocations if item.deposit_date]
    target_rows = frappe.get_all(
        "CN Remittance Target",
        filters={"parent": ["in", registered_names]},
        fields=[
            "name", "parent", "period", "row_key", "historical_application",
            "complementary_item", "amount_usd", "result",
        ],
        order_by="parent asc, idx asc",
        limit_page_length=100000,
    ) if registered_names else []
    allocation_instructions = [
        (item, target) for item in manual_allocations if item.deposit_date
        for target in target_rows if target.parent == item.name
    ] + [
        (item, item) for item in manual_allocations if not item.deposit_date
    ]
    for parent, item in allocation_instructions:
        if parent.deposit_date:
            candidates = [
                deposit for deposit in deposits
                if deposit["id"] == registered_ids.get(parent.name)
            ]
        else:
            candidates = [
                deposit for deposit in deposits_by_reference[clean_text(parent.deposit_reference)]
                if not parent.deposit_voucher
                or clean_text(deposit_meta[deposit["id"]]["account"].voucher)
                == clean_text(parent.deposit_voucher)
            ]
        if len(candidates) != 1:
            manual_results[item.name] = (
                "Falta deposito" if not candidates else "Deposito ambiguo"
            )
            manually_ambiguous_deposits.update(
                deposit["id"] for deposit in candidates
            )
            continue
        claim_id = (
            "X:" + item.complementary_item if item.complementary_item
            else "H:" + item.historical_application if item.historical_application
            else "C:" + row_by_key.get((item.period, clean_text(item.row_key)), "")
        )
        claim_group = clean_text(claims_by_id.get(claim_id, {}).get("group"))
        deposit_group = clean_text(candidates[0].get("group"))
        if claim_group and deposit_group and claim_group != deposit_group:
            manual_results[item.name] = "Empresa no coincide"
            manually_ambiguous_deposits.add(candidates[0]["id"])
            continue
        instructions.append(
            {"id": item.name, "deposit_id": candidates[0]["id"],
             "claim_id": claim_id, "amount_usd": flt(item.amount_usd)}
        )

    detail_context = _prepare_remittance_details(
        manual_allocations, registered_ids, deposits, claims, instructions,
        tolerance_by_employer,
    )
    instructions.extend(detail_context["instructions"])
    previously_blocked = set(manually_ambiguous_deposits)
    manually_ambiguous_deposits.update(detail_context["blocked_deposits"])
    result = allocate_cash(
        deposits, claims, instructions, manually_ambiguous_deposits
    )
    detail_statuses = _sync_remittance_details(detail_context, result)
    movements = rounding_movements(
        deposits, claims, result["allocations"], result["deposit_remaining"],
        result["claim_remaining"], tolerance_by_employer,
        result["blocked_deposits"] - (
            detail_context["rounding_eligible"] - previously_blocked
        ),
    )
    result["rounding_movements"] = movements
    for movement in movements:
        deposit_id = movement["deposit_id"]
        result["deposit_remaining"][deposit_id] = round(
            result["deposit_remaining"][deposit_id]
            - movement["consumed_residual_usd"], 4
        )
    manual_results.update(result["instruction_results"])
    cash_by_claim = defaultdict(float)
    for entry in result["allocations"]:
        cash_by_claim[entry["claim_id"]] += flt(entry["amount_usd"])
    for target in target_rows:
        status = manual_results.get(target.name, "Pendiente")
        if target.result != status:
            frappe.db.set_value(
                "CN Remittance Target", target.name, "result", status,
                update_modified=False,
            )
    for item in manual_allocations:
        if item.deposit_date:
            deposit_id = registered_ids[item.name]
            allocated = round(
                flt(item.amount_usd) - flt(result["deposit_remaining"].get(deposit_id, item.amount_usd)), 4
            )
            remaining = round(flt(item.amount_usd) - allocated, 4)
            statuses = [manual_results.get(row.name, "Pendiente") for row in target_rows if row.parent == item.name]
            application_pending = any(
                entry["deposit_id"] == deposit_id
                and entry["claim_id"].startswith("C:")
                and cash_by_claim[entry["claim_id"]]
                > flt(claims_by_id[entry["claim_id"]]["core_applied_usd"]) + CASH_EPSILON
                for entry in result["allocations"]
            )
            status = (
                "Revisar destinos" if any(value != "Aplicada" for value in statuses)
                else "Revisar detalle" if detail_statuses.get(item.name) in {
                    "Revisar filas", "Detalle supera depósito", "Importar detalle actualizado",
                }
                else "Detalle pendiente" if detail_statuses.get(item.name) == "Detalle pendiente"
                else (
                    "Distribuido, aplicación pendiente" if remaining <= CASH_EPSILON
                    else "Parcial, aplicación pendiente"
                ) if application_pending
                else "Conciliado" if remaining <= CASH_EPSILON
                else "Parcial" if allocated > CASH_EPSILON
                else "Sin aplicación"
            )
            if item.result != status or flt(item.allocated_usd) != allocated or flt(item.unallocated_usd) != remaining:
                frappe.db.set_value(
                    "CN Remittance Allocation", item.name,
                    {"result": status, "allocated_usd": allocated, "unallocated_usd": remaining},
                    update_modified=False,
                )
        else:
            status = manual_results.get(item.name, "Pendiente")
            if item.result != status:
                frappe.db.set_value(
                    "CN Remittance Allocation", item.name, "result", status,
                    update_modified=False,
                )

    assigned_by_deposit = defaultdict(float)
    detail_by_deposit = defaultdict(list)
    for entry in result["allocations"]:
        assigned_by_deposit[entry["deposit_id"]] += entry["amount_usd"]
        claim_id = entry["claim_id"]
        if claim_id.startswith("C:"):
            target = rows_by_name[claim_id[2:]]
            destination = {
                "tipo": "Cobranza", "periodo": target.parent,
                "fila_id": target.row_key, "credito": target.loan_number,
            }
        elif claim_id.startswith("H:"):
            application = historical_applications[claim_id[2:]]
            destination = {
                "tipo": "Aplicacion historica",
                "periodo": application.historical_period,
                "aplicacion_id": application.name,
                "credito": application.loan_number,
            }
        else:
            destination = {
                "tipo": "Partida complementaria", "partida": claim_id[2:]
            }
        detail_by_deposit[entry["deposit_id"]].append(
            {**destination, "importe_usd": entry["amount_usd"],
             "origen": entry["origin"]}
        )
    for movement in movements:
        deposit_id = movement["deposit_id"]
        assigned_by_deposit[deposit_id] += movement["consumed_residual_usd"]
        detail_by_deposit[deposit_id].append({
            "tipo": "Movimiento de conciliación",
            "periodo": movement["period"],
            "movimiento": movement["name"],
            "diferencia_usd": movement["signed_amount_usd"],
            "importe_usd": movement["consumed_residual_usd"],
            "origen": "Tolerancia automática",
        })
    for deposit_id, meta in deposit_meta.items():
        assigned = round(assigned_by_deposit[deposit_id], 4)
        remaining = round(result["deposit_remaining"][deposit_id], 4)
        reason = _("Distribuido {0} US$; pendiente de distribuir {1} US$.").format(
            assigned, remaining
        )
        if deposit_id in result["blocked_deposits"]:
            reason += " " + _("Revise el detalle por cliente o una distribución manual inválida o ambigua.")
        if deposit_id in unresolved_employer_ids:
            reason += " " + _(
                "La empresa del depósito no se identificó de forma única; "
                "se requiere una distribución manual para asignarlo."
            )
        for source in (meta["account"], meta["bank"]):
            source.allocated_usd = assigned
            source.unallocated_usd = remaining
            source.allocation_reason = reason
            source.allocation_detail = json.dumps(
                detail_by_deposit[deposit_id], ensure_ascii=False
            )
    result["deposit_meta"] = deposit_meta
    result["registered_ids"] = registered_ids
    result["complementary_target"] = complementary_target
    result["complementary_totals"] = complementary_totals
    return result


def _classify_surplus(allocation, surplus_items):
    """Document unapplied cash, without treating it as a loan or fee payment."""
    meta_by_id = allocation["deposit_meta"]
    justified = defaultdict(float)
    for item in surplus_items:
        if item.registered_deposit:
            selected = allocation["registered_ids"].get(item.registered_deposit)
            candidates = [selected] if selected in meta_by_id else []
        else:
            candidates = [
                deposit_id for deposit_id, meta in meta_by_id.items()
                if clean_text(meta["account"].reference) == clean_text(item.deposit_reference)
                and (
                    not item.deposit_voucher
                    or clean_text(meta["account"].voucher)
                    == clean_text(item.deposit_voucher)
                )
            ]
        if len(candidates) != 1:
            status = "Falta deposito" if not candidates else "Deposito ambiguo"
        else:
            deposit_id = candidates[0]
            if not can_document_surplus(
                allocation["deposit_remaining"][deposit_id],
                justified[deposit_id],
                item.amount_usd,
            ):
                status = "Excede saldo sin distribuir"
            else:
                justified[deposit_id] += flt(item.amount_usd)
                status = "Saldo a favor documentado"
        if item.result != status:
            frappe.db.set_value(
                "CN Deposit Surplus", item.name, "result", status,
                update_modified=False,
            )
    for deposit_id, meta in meta_by_id.items():
        total = flt(allocation["deposit_remaining"][deposit_id])
        company_credit = round(justified[deposit_id], 4)
        unclassified = round(max(total - company_credit, 0), 4)
        for source in (meta["account"], meta["bank"]):
            source.justified_surplus_usd = company_credit
            source.unclassified_usd = unclassified
            if company_credit:
                source.allocation_reason += " " + _(
                    "Saldo a favor documentado de la empresa: {0} US$; no aplicado al credito."
                ).format(company_credit)


def _sync_registered_deposit_detail(allocation):
    for name, deposit_id in allocation["registered_ids"].items():
        meta = allocation["deposit_meta"].get(deposit_id)
        if not meta:
            continue
        source = meta["account"]
        current_result = frappe.db.get_value("CN Remittance Allocation", name, "result")
        result = current_result
        if (
            current_result in {"Parcial", "Sin aplicación"}
            and flt(source.unallocated_usd) > CASH_EPSILON
            and flt(source.unclassified_usd) <= CASH_EPSILON
            and flt(source.justified_surplus_usd) > CASH_EPSILON
        ):
            result = (
                "Parcial con saldo a favor" if flt(source.allocated_usd) > CASH_EPSILON
                else "Saldo a favor documentado"
            )
        frappe.db.set_value(
            "CN Remittance Allocation", name,
            {
                "result": result,
                "allocation_detail": source.allocation_detail,
                "inherited_exception_comment": getattr(source, "inherited_exception_comment", ""),
                "justified_surplus_usd": flt(source.justified_surplus_usd),
                "unclassified_usd": flt(source.unclassified_usd),
            },
            update_modified=False,
        )


def _sync_rounding_movements(movements, allocation, source_rows):
    """Persist deterministic movements and reverse stale ones; never post GL."""
    desired = {movement["name"]: movement for movement in movements}
    existing = {
        item.name: item
        for item in frappe.get_all(
            "CN Reconciliation Movement",
            fields=["name", "status", "period"],
            limit_page_length=100000,
        )
    }
    for name, item in existing.items():
        if item.status != "Vigente" or name in desired:
            continue
        if frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
            frappe.throw(_("El movimiento {0} pertenece a un período cerrado y no puede revertirse automáticamente.").format(name))
        document = frappe.get_doc("CN Reconciliation Movement", name)
        document.status = "Revertido"
        document.reversed_on = now_datetime()
        document.reversal_reason = _(
            "La diferencia ya no cumple la tolerancia, la referencia o el enlace único entre aplicación y depósito."
        )
        document.save(ignore_permissions=True)

    source_by_name = {row.name: row for row in source_rows}
    detail_by_source = defaultdict(list)
    for movement in movements:
        name = movement["name"]
        period = movement["period"]
        if name not in existing or existing[name].status != "Vigente":
            if frappe.db.get_value("CN Reconciliation Period", period, "status") == "Cerrado":
                frappe.throw(_("No se puede crear o reactivar un ajuste en el período cerrado {0}.").format(period))
            if name in existing:
                document = frappe.get_doc("CN Reconciliation Movement", name)
                document.status = "Vigente"
                document.reversed_on = None
                document.reversal_reason = ""
                document.save(ignore_permissions=True)
            else:
                deposit = allocation["deposit_meta"][movement["deposit_id"]]["account"]
                frappe.get_doc({
                    "doctype": "CN Reconciliation Movement",
                    "movement_key": name,
                    "status": "Vigente",
                    "employer": movement["employer"],
                    "period": period,
                    "deposit_source_row": movement["deposit_id"],
                    "application_source_row": movement["application_id"],
                    "claim_id": movement["claim_id"],
                    "deposit_reference": deposit.reference,
                    "deposit_date": deposit.event_date,
                    "signed_amount_usd": movement["signed_amount_usd"],
                    "absorbed_cash_usd": movement["consumed_residual_usd"],
                    "tolerance_usd": movement["tolerance_usd"],
                    "core_applied_usd": movement["core_applied_usd"],
                    "deposit_usd": movement["deposit_usd"],
                    "claim_usd": movement["claim_usd"],
                    "reason": _(
                        "Diferencia menor dentro de la tolerancia autorizada; movimiento interno de conciliación, sin asiento contable ni cambio en el core."
                    ),
                }).insert(ignore_permissions=True)
        for source_id in (
            movement["deposit_id"],
            allocation["deposit_meta"][movement["deposit_id"]]["bank"].name,
            movement["application_id"],
        ):
            detail_by_source[source_id].append({
                "movimiento": name,
                "diferencia_usd": movement["signed_amount_usd"],
                "periodo": period,
            })
    for source_id, entries in detail_by_source.items():
        source = source_by_name.get(source_id)
        if source:
            source.rounding_adjustment_usd = round(
                sum(flt(entry["diferencia_usd"]) for entry in entries), 4
            )
            source.rounding_movement_detail = json.dumps(entries, ensure_ascii=False)


def _rebuild_period_balances(
    periods, source_rows, deposit_pairs, allocation,
):
    rows_by_name = {}
    for period in periods:
        for row in period.collection_rows:
            row.applied_usd = 0
            row.applied_nio = 0
            row.complementary_usd = allocation["complementary_totals"][row.name]
            row.remitted_usd = 0
            row.remitted_nio = 0
            row.fx_variance_usd = 0
            row.rounding_adjustment_usd = 0
            row.application_status = "Pendiente"
            row.remittance_detail = "[]"
            row.inherited_exception_comment = ""
            rows_by_name[row.name] = row

    for source in source_rows:
        if (
            source.event_type != "Aplicacion" or not source.effective
            or source.match_status != "Conciliado"
        ):
            continue
        for link in _application_allocations(source):
            target = rows_by_name.get(link["collection_row_id"])
            if not target:
                continue
            amount = flt(link["amount_usd"])
            target.applied_usd = flt(target.applied_usd) + amount
            equivalent_currency, equivalent_amount = _equivalent_amount(
                target, source, amount
            )
            if equivalent_currency == "NIO":
                target.applied_nio = flt(target.applied_nio) + equivalent_amount

    detail_by_target = defaultdict(list)
    for entry in allocation["allocations"]:
        claim_id = entry["claim_id"]
        target_name = (
            claim_id[2:] if claim_id.startswith("C:")
            else allocation["complementary_target"].get(claim_id[2:])
        )
        target = rows_by_name.get(target_name)
        if not target:
            continue
        amount = entry["amount_usd"]
        deposit_account = allocation["deposit_meta"][entry["deposit_id"]]["account"]
        detail_by_target[target.name].append(
            {
                "referencia": deposit_account.reference,
                "comprobante": deposit_account.voucher,
                "fecha": str(deposit_account.event_date or ""),
                "importe_usd": amount,
                "destino": "Partida complementaria" if claim_id.startswith("X:") else "Cobranza",
                "origen": entry["origin"],
            }
        )
        target.remitted_usd = flt(target.remitted_usd) + amount
        deposit_rate = allocation["deposit_meta"][entry["deposit_id"]]["nio_per_usd"]
        collection_rate = (
            flt(target.expected_nio) / flt(target.expected_usd)
            if flt(target.expected_usd) > AMOUNT_TOLERANCE else 0
        )
        target.remitted_nio = flt(target.remitted_nio) + amount * (
            deposit_rate or collection_rate
        )

    for movement in allocation["rounding_movements"]:
        if not movement["claim_id"].startswith("C:"):
            continue
        target = rows_by_name.get(movement["claim_id"][2:])
        if not target:
            continue
        delta = flt(movement["signed_amount_usd"])
        consumed = flt(movement["consumed_residual_usd"])
        target.rounding_adjustment_usd = flt(target.rounding_adjustment_usd) + delta
        target.remitted_usd = flt(target.remitted_usd) + consumed
        rate = allocation["deposit_meta"][movement["deposit_id"]]["nio_per_usd"]
        if not rate and flt(target.expected_usd) > CASH_EPSILON:
            rate = flt(target.expected_nio) / flt(target.expected_usd)
        target.remitted_nio = flt(target.remitted_nio) + consumed * rate
        account = allocation["deposit_meta"][movement["deposit_id"]]["account"]
        detail_by_target[target.name].append({
            "referencia": account.reference,
            "comprobante": account.voucher,
            "fecha": str(account.event_date or ""),
            "importe_usd": consumed,
            "diferencia_usd": delta,
            "movimiento": movement["name"],
            "destino": "Movimiento de conciliación",
            "origen": "Tolerancia automática",
        })

    for target in rows_by_name.values():
        deducted_usd = _deducted_amount(target, "USD")
        core_due = max(deducted_usd - flt(target.complementary_usd), 0)
        status_due = (
            max(flt(target.expected_usd) - flt(target.complementary_usd), 0)
            if target.deduction_status in {"", "Pendiente de detalle"}
            else core_due
        )
        if (
            target.deduction_currency == "NIO"
            and flt(target.deducted_nio) > AMOUNT_TOLERANCE
            and flt(target.remitted_usd) > AMOUNT_TOLERANCE
            and same_amount(target.remitted_nio, target.deducted_nio)
        ):
            variance = round(deducted_usd - flt(target.remitted_usd), 4)
            if abs(variance) > AMOUNT_TOLERANCE:
                target.fx_variance_usd = variance
                target.application_status = "Diferencia cambiaria en revision"
                continue
        cash_due = max(deducted_usd - max(flt(target.fx_variance_usd), 0), 0)
        rounding = flt(target.rounding_adjustment_usd)
        core_complete = (
            flt(target.applied_usd) + max(rounding, 0) + CASH_EPSILON >= core_due
        )
        cash_complete = (
            flt(target.remitted_usd) + max(-rounding, 0) + CASH_EPSILON >= cash_due
        )
        loan_cash = flt(target.remitted_usd) - flt(target.complementary_usd)
        unexplained = loan_cash - flt(target.applied_usd) - rounding
        if (
            core_complete and cash_complete
            and not flt(target.fx_variance_usd)
            and abs(unexplained) > CASH_EPSILON
        ):
            target.application_status = "Diferencia aplicacion vs deposito"
            continue
        if (
            deducted_usd > AMOUNT_TOLERANCE
            and core_complete and cash_complete
        ):
            target.application_status = "Aplicado y remitido"
        elif deducted_usd > AMOUNT_TOLERANCE and cash_complete:
            target.application_status = "Remitido, aplicacion parcial"
        elif flt(target.remitted_usd) > AMOUNT_TOLERANCE:
            target.application_status = "Remesa parcial"
        elif flt(target.applied_usd) > AMOUNT_TOLERANCE:
            target.application_status = (
                "Aplicacion parcial"
                if flt(target.applied_usd) + AMOUNT_TOLERANCE < status_due
                else "Aplicacion encontrada"
            )

    _transfer_matching_exception_notes(rows_by_name, detail_by_target, allocation)

    paired_references = {
        clean_text(account.reference) for account, _bank in deposit_pairs
    }
    for source in source_rows:
        if source.event_type != "Aplicacion" or not source.effective:
            continue
        targets = [
            rows_by_name[link["collection_row_id"]]
            for link in _application_allocations(source)
            if link["collection_row_id"] in rows_by_name
        ]
        if not targets:
            source.deposit_match_status = (
                "Ambiguo" if clean_text(source.reference) in paired_references
                else "Sin deposito"
            )
            source.deposit_match_reason = _(
                "La aplicacion aun no se enlaza de forma unica con una cobranza."
            )
            continue
        complete = all(
            flt(target.remitted_usd) + max(flt(target.fx_variance_usd), 0)
            + max(-flt(target.rounding_adjustment_usd), 0)
            + CASH_EPSILON >= _deducted_amount(target, "USD")
            for target in targets
        )
        if any(target.application_status == "Diferencia aplicacion vs deposito" for target in targets):
            source.deposit_match_status = "Diferencia de importe"
        elif complete:
            source.deposit_match_status = "Remesa conciliada"
        elif any(flt(target.remitted_usd) > AMOUNT_TOLERANCE for target in targets):
            source.deposit_match_status = "Remesa parcial"
        else:
            source.deposit_match_status = "Sin deposito"
        source.deposit_match_reason = _(
            "{0} cobranza(s): {1} US$ deducidos, {2} US$ remitidos; {3} US$ aplicados al credito; ajuste de conciliación {4} US$."
        ).format(
            len(targets),
            round(sum(_deducted_amount(target, "USD") for target in targets), 4),
            round(sum(flt(target.remitted_usd) for target in targets), 4),
            round(sum(flt(target.applied_usd) for target in targets), 4),
            round(sum(flt(target.rounding_adjustment_usd) for target in targets), 4),
        )
        source.fx_variance_usd = sum(flt(target.fx_variance_usd) for target in targets)

    for period in periods:
        period.recalculate_totals()
        relevant = [
            row for row in period.collection_rows
            if flt(row.deducted_usd) > AMOUNT_TOLERANCE
            or flt(row.deducted_nio) > AMOUNT_TOLERANCE
        ]
        if period.status != "Cerrado":
            if relevant and all(
                row.application_status == "Aplicado y remitido" for row in relevant
            ):
                period.status = "Deposito conciliado"
            elif period.status == "Deposito conciliado":
                period.status = "Detalle empresa cargado"
        period.save(ignore_permissions=True)


def _transfer_matching_exception_notes(rows_by_name, detail_by_target, allocation):
    """Show first-stage evidence on related deposits without changing cash balances."""
    exceptions_by_row = defaultdict(list)
    if rows_by_name:
        exceptions = frappe.get_all(
            "CN Reconciliation Exception",
            filters={
                "collection_row_id": ["in", list(rows_by_name)],
                "status": ["in", ["Abierta", "En revision", "Resuelta"]],
            },
            fields=[
                "name", "collection_row_id", "exception_type", "description",
                "resolution", "status",
            ],
            order_by="creation desc",
            limit_page_length=100000,
        )
        for exception in exceptions:
            exceptions_by_row[exception.collection_row_id].append(exception)

    notes_by_key = {}
    for target in rows_by_name.values():
        deduction_notes = [
            {
                "comment": (
                    exception.resolution
                    if exception.status == "Resuelta" and exception.resolution
                    else exception.description
                ),
                "exception_id": exception.name,
            }
            for exception in exceptions_by_row[target.name]
            if exception.exception_type in {
                "Deduccion parcial", "No deducido", "Deduccion en exceso",
                "Moneda no coincide", "Importes inconsistentes",
            }
        ]
        application_notes = [
            {"comment": comment}
            for comment in (target.first_exception_comment, target.application_comment)
            if clean_text(comment)
        ]
        notes = matching_exception_notes(
            expected_usd=flt(target.expected_usd),
            deducted_usd=_deducted_amount(target, "USD"),
            applied_usd=flt(target.applied_usd),
            complementary_usd=flt(target.complementary_usd),
            remitted_usd=flt(target.remitted_usd),
            deduction_notes=deduction_notes,
            application_notes=application_notes,
        )
        notes_by_key[(target.parent, clean_text(target.row_key))] = notes
        target.inherited_exception_comment = "\n".join(
            f"{note['origin']}: {note['gap_usd']} US$ — {note['comment']}"
            for note in notes
        )
        for detail in detail_by_target[target.name]:
            if detail["destino"] == "Cobranza" and notes:
                detail["excepciones_heredadas"] = notes
        target.remittance_detail = json.dumps(
            detail_by_target[target.name], ensure_ascii=False
        )

    for meta in allocation["deposit_meta"].values():
        source = meta["account"]
        entries = json.loads(source.allocation_detail or "[]")
        summaries = []
        seen = set()
        for entry in entries:
            if entry.get("tipo") != "Cobranza":
                continue
            notes = notes_by_key.get(
                (entry.get("periodo"), clean_text(entry.get("fila_id"))), []
            )
            if not notes:
                continue
            entry["excepciones_heredadas"] = notes
            for note in notes:
                key = (entry.get("periodo"), entry.get("fila_id"), note["comment"])
                if key not in seen:
                    seen.add(key)
                    summaries.append(
                        f"{entry['periodo']} / {entry['fila_id']}: "
                        f"{note['gap_usd']} US$ — {note['comment']}"
                    )
        encoded = json.dumps(entries, ensure_ascii=False)
        summary = "\n".join(summaries)
        for deposit in (meta["account"], meta["bank"]):
            deposit.allocation_detail = encoded
            deposit.inherited_exception_comment = summary


def _rebuild_historical_balances(periods, source_rows, allocation):
    """Second-stage-only backfill: core applications versus paired deposits."""
    historical_periods = {
        period.name: period for period in periods
        if period.reconciliation_mode == "Historica"
    }
    applications = {
        row.name: row for row in source_rows
        if row.event_type == "Aplicacion" and row.effective
        and row.historical_period in historical_periods
        and row.match_status == "Conciliado"
    }
    deposits_by_application = defaultdict(list)
    unclassified_deposits_by_period = defaultdict(set)
    for entry in allocation["allocations"]:
        if not entry["claim_id"].startswith("H:"):
            continue
        application_id = entry["claim_id"][2:]
        if application_id not in applications:
            continue
        account = allocation["deposit_meta"][entry["deposit_id"]]["account"]
        if flt(account.unclassified_usd) > CASH_EPSILON:
            unclassified_deposits_by_period[
                applications[application_id].historical_period
            ].add(entry["deposit_id"])
        deposits_by_application[application_id].append(
            {
                "referencia": account.reference,
                "comprobante": account.voucher,
                "fecha": str(account.event_date or ""),
                "importe_usd": entry["amount_usd"],
                "origen": entry["origin"],
            }
        )

    rounding_by_application = defaultdict(float)
    for movement in allocation["rounding_movements"]:
        if not movement["claim_id"].startswith("H:"):
            continue
        application_id = movement["claim_id"][2:]
        if application_id not in applications:
            continue
        rounding_by_application[application_id] += flt(movement["signed_amount_usd"])
        account = allocation["deposit_meta"][movement["deposit_id"]]["account"]
        deposits_by_application[application_id].append({
            "referencia": account.reference,
            "comprobante": account.voucher,
            "fecha": str(account.event_date or ""),
            "importe_usd": flt(movement["consumed_residual_usd"]),
            "diferencia_usd": flt(movement["signed_amount_usd"]),
            "movimiento": movement["name"],
            "origen": "Tolerancia automática",
        })

    apps_by_period = defaultdict(list)
    for application in applications.values():
        details = deposits_by_application[application.name]
        remitted = round(sum(flt(item["importe_usd"]) for item in details), 4)
        applied = flt(application.amount)
        adjustment = round(rounding_by_application[application.name], 4)
        application.historical_remitted_usd = remitted
        application.historical_balance_usd = historical_balance(applied + adjustment, remitted)
        application.historical_detail = json.dumps(details, ensure_ascii=False)
        application.deposit_match_status = (
            "Remesa conciliada" if application.historical_balance_usd <= CASH_EPSILON
            else "Remesa parcial" if remitted > CASH_EPSILON
            else "Sin deposito"
        )
        application.deposit_match_reason = _(
            "Histórico: {0} US$ aplicados al crédito; ajuste {1} US$; {2} US$ vinculados a depósitos; {3} US$ pendientes de evidencia de depósito."
        ).format(
            round(applied, 4), adjustment, remitted, application.historical_balance_usd,
        )
        apps_by_period[application.historical_period].append(application)

    for period in historical_periods.values():
        related = apps_by_period[period.name]
        applied = round(sum(flt(row.amount) for row in related), 4)
        remitted = round(sum(flt(row.historical_remitted_usd) for row in related), 4)
        adjustment = round(sum(rounding_by_application[row.name] for row in related), 4)
        fingerprint_data = [
            {
                "application_id": row.name,
                "amount_usd": round(flt(row.amount), 4),
                "deposits": sorted(
                    deposits_by_application[row.name],
                    key=lambda item: (
                        clean_text(item["referencia"]),
                        clean_text(item["comprobante"]),
                        clean_text(item["fecha"]),
                        flt(item["importe_usd"]),
                    ),
                ),
            }
            for row in sorted(related, key=lambda item: item.name)
        ]
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if period.status == "Cerrado" and (
            not same_amount(period.applied_usd, applied)
            or not same_amount(period.remitted_usd, remitted)
            or abs(flt(period.rounding_adjustment_usd) - adjustment) > CASH_EPSILON
            or (
                period.historical_fingerprint
                and period.historical_fingerprint != fingerprint
            )
            or bool(unclassified_deposits_by_period[period.name])
        ):
            frappe.throw(
                _("El período histórico {0} está cerrado y su conciliación cambiaría.").format(period.name)
            )
        period.expected_usd = 0
        period.expected_nio = 0
        period.deducted_usd = 0
        period.deducted_nio = 0
        period.applied_usd = applied
        period.applied_nio = 0
        period.remitted_usd = remitted
        period.remitted_nio = 0
        period.fx_variance_usd = 0
        period.rounding_adjustment_usd = adjustment
        period.historical_fingerprint = fingerprint
        period.exception_count = sum(
            row.deposit_match_status != "Remesa conciliada" for row in related
        ) + len(unclassified_deposits_by_period[period.name])
        if period.status != "Cerrado":
            period.status = (
                "Historico con excedente"
                if unclassified_deposits_by_period[period.name]
                else historical_status(applied + adjustment, remitted)
            )
        period.save(ignore_permissions=True)


def _equivalent_amount(target, source, amount_usd):
    if (
        source.currency == "USD"
        and flt(target.expected_usd) > AMOUNT_TOLERANCE
        and flt(target.expected_nio) > AMOUNT_TOLERANCE
    ):
        return "NIO", amount_usd * flt(target.expected_nio) / flt(target.expected_usd)
    if (
        source.currency == "NIO"
        and flt(target.expected_nio) > AMOUNT_TOLERANCE
        and flt(target.expected_usd) > AMOUNT_TOLERANCE
    ):
        return "USD", amount_usd * flt(target.expected_usd) / flt(target.expected_nio)
    return None, 0
