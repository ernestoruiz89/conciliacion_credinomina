from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from contextvars import ContextVar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

from credinomina_reconciliation.allocation import allocate_cash, can_document_surplus
from credinomina_reconciliation.accounting_naming import (
    accounting_month, accounting_prefix, new_accounting_name, rename_accounting_import,
)
from credinomina_reconciliation.company_credit import CATEGORY as COMPANY_CREDIT
from credinomina_reconciliation.tolerance_items import (
    CATEGORY as TOLERANCE_CATEGORY, item_values, tolerance_item_write,
)
from credinomina_reconciliation.cadence import unique_full_quincena_pair
from credinomina_reconciliation.client_registry import (
    enrich_source_import_clients,
    load_client_index,
    names_for_claim,
)
from credinomina_reconciliation.client_identity import choose_client
from credinomina_reconciliation.credit_portfolio import enrich_accounting_records
from credinomina_reconciliation.deduction_recognition import recognition_reason
from credinomina_reconciliation.deposit_scoping import (
    resolved_deposit_employer,
)
from credinomina_reconciliation.employer_naming import (
    AccountingEmployerResolver,
    UNIDENTIFIED_EMPLOYER,
    attach_employer_aliases,
    employer_alias_index,
)
from credinomina_reconciliation.historical import (
    blocked_historical_deposits,
    historical_balance,
    historical_scope_contains,
    historical_status,
    is_historical_date,
)
from credinomina_reconciliation.parsers import (
    SourceFileError,
    canonical_identifier,
    clean_text,
    file_sha256,
    apply_accounting_currency_override,
    parse_accounting_movements,
)
from credinomina_reconciliation.period_lock import period_write_action
from credinomina_reconciliation.reconciliation import (
    AMOUNT_TOLERANCE,
    application_matches_collection,
    complementary_matches_collection,
    converted_amount,
    deposit_pair_result,
    documented_rate,
    matching_exception_notes,
    narrow_deposit_candidates_by_date,
    remittance_fx_basis,
    same_amount,
)
from credinomina_reconciliation.rounding import (
    CASH_EPSILON, decimal_value, money, money_float, rounding_movements, sum_money,
)
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


PROVISIONAL_APPLICATION = "Enlace provisional"
from credinomina_reconciliation.application_adjustments import net_amount, refresh_rows, guard_source_changes, CATEGORY as APPLICATION_ADJUSTMENT

SETTLED_APPLICATION_STATUSES = {"Depósito conciliado", "Aplicación compensada"}
LINKED_APPLICATION_STATUSES = {"Conciliado", PROVISIONAL_APPLICATION}
_source_reconcile_verified = ContextVar("cn_source_reconcile_verified", default=False)

_SOURCE_EVIDENCE_FIELDS = (
    "tmov", "tdoc", "accounting_classification", "classification_reason", "source_classification",
    "source_account", "source_debit", "source_credit", "source_currency", "accounting_reference",
    "accounting_source_key", "complementary_item", "source_description", "source_fx_rate", "remittance_allocation",
    "source_row", "source_key", "event_type", "event_date", "client_name",
    "client_number", "employee_number", "loan_number", "amount",
    "accounting_entry", "receipt", "reference", "voucher", "employer_text",
    "national_id", "installment_number", "currency", "amount_usd",
    "amount_nio", "equivalent_currency", "equivalent_amount", "fx_rate",
    "fx_basis", "manual_fx_rate", "description",
    "processing_route", "historical_period",
    "portfolio_snapshot_used", "portfolio_client_name", "portfolio_client", "client",
    "client_registry_status", "portfolio_employer", "portfolio_credit_status", "portfolio_credit_lifecycle",
    "portfolio_validation_status",
)
_SOURCE_DERIVED_FIELDS = (
    "application_adjustment_usd", "net_applied_usd", "application_adjustment_status",
    "historical_application_id", "effective", "match_status", "match_reason",
    "collection_period", "collection_row_id", "application_allocation_detail",
    "deposit_match_status", "deposit_match_reason", "historical_remitted_usd",
    "historical_balance_usd", "historical_detail", "allocated_usd",
    "unallocated_usd", "justified_surplus_usd", "unclassified_usd",
    "allocation_reason", "allocation_detail", "inherited_exception_comment",
    "complementary_usd", "fx_variance_usd", "rounding_adjustment_usd",
    "rounding_movement_detail",
)
_IMPORT_EVIDENCE_FIELDS = (
    "source_file", "file_hash", "historical_backfill",
    "employer", "historical_period", "currency", "manual_fx_rate",
    "portfolio_snapshot",
)


def _source_linked_periods(row):
    """Resolve all persisted period links, not only the first payroll half."""
    periods = {row.get("collection_period"), row.get("historical_period")}
    for entry in _detail_entries(row.get("application_allocation_detail")):
        if isinstance(entry, dict):
            periods.add(entry.get("period") or entry.get("periodo"))
            collection_row_id = entry.get("collection_row_id")
            if collection_row_id:
                periods.add(frappe.db.get_value(
                    "CN Collection Row", collection_row_id, "parent"
                ))
    for entry in _detail_entries(row.get("allocation_detail")):
        if not isinstance(entry, dict):
            continue
        periods.add(entry.get("periodo") or entry.get("period"))
        if entry.get("partida"):
            periods.add(frappe.db.get_value(
                "CN Complementary Item", entry["partida"], "period"
            ))
    if row.get("collection_row_id"):
        periods.add(frappe.db.get_value(
            "CN Collection Row", row.collection_row_id, "parent"
        ))
    return sorted(period for period in periods if period)


class CNAccountingImport(Document):
    def autoname(self):
        self.name = new_accounting_name(accounting_prefix(self.employer, accounting_month(self.rows or [])))

    def on_update(self):
        rename_accounting_import(self)

    def on_trash(self):
        self._assert_no_closed_period_links(self.rows or [])
        if self.rows and frappe.db.exists("CN Complementary Item", {"related_application": ["in", [row.name for row in self.rows]]}):
            frappe.throw(_("La importación tiene partidas vinculadas a sus aplicaciones; conserve el registro original."))

    def validate(self):
        self._validate_bulk_scope()
        self._validate_employer_scope()
        self._validate_historical_periods()
        self._validate_duplicate_file()
        self._validate_manual_rates()
        guard_source_changes(self)
        refresh_rows(self.rows or [])
        self._validate_closed_source_edits()
        self.recalculate_summary()

    def _validate_bulk_scope(self):
        previous = self.get_doc_before_save()
        if previous and any(str(previous.get(field) or "") != str(self.get(field) or "")
                            for field in ("bulk_source_hash", "bulk_event_date", "bulk_source_file")):
            frappe.throw(_("No se puede cambiar el origen ni la fecha de una carga masiva."))
        if self.bulk_source_hash and any(
            row.event_type != "Aplicacion" or not row.event_date
            or getdate(row.event_date) != getdate(self.bulk_event_date)
            for row in self.rows or []
        ):
            frappe.throw(_("Todas las filas de la carga masiva deben ser aplicaciones de la misma fecha."))

    def _validate_closed_source_edits(self):
        previous = self.get_doc_before_save()
        if self.historical_period and frappe.db.get_value(
            "CN Reconciliation Period", self.historical_period, "status"
        ) == "Cerrado" and (
            not previous or previous.historical_period != self.historical_period
        ):
            frappe.throw(_(
                "El período histórico {0} está cerrado. Use Reabrir período "
                "antes de asignar una nueva importación."
            ).format(self.historical_period))
        if not previous:
            self._assert_no_closed_period_links(self.rows or [])
            return
        old_rows = {row.name: row for row in previous.rows or [] if row.name}
        new_rows = {row.name: row for row in self.rows or [] if row.name}
        parent_changed = any(
            str(previous.get(field) or "") != str(self.get(field) or "")
            for field in _IMPORT_EVIDENCE_FIELDS
        ) or (
            previous.status != self.status
            and not _source_reconcile_verified.get()
        )
        fields = _SOURCE_EVIDENCE_FIELDS
        if not _source_reconcile_verified.get():
            fields += _SOURCE_DERIVED_FIELDS
        candidates = []
        for name, old_row in old_rows.items():
            new_row = new_rows.get(name)
            if parent_changed or not new_row or any(
                str(old_row.get(field) or "") != str(new_row.get(field) or "")
                for field in fields
            ):
                candidates.append(old_row)
        candidates.extend(
            row for row in self.rows or []
            if not row.name or row.name not in old_rows
        )
        self._assert_no_closed_period_links(candidates)

    def _assert_no_closed_period_links(self, rows):
        for row in rows:
            linked_periods = set(_source_linked_periods(row))
            # New application rows inherit the import's historical period at
            # reconciliation time, even before a row-level link is persisted.
            if row.get("event_type") == "Aplicacion" and self.historical_period:
                linked_periods.add(self.historical_period)
            for period_name in sorted(linked_periods):
                if frappe.db.get_value(
                    "CN Reconciliation Period", period_name, "status"
                ) == "Cerrado":
                    frappe.throw(_(
                        "La importación {0} contiene la fila {1} vinculada al período "
                        "cerrado {2}. Use Reabrir período antes de cambiar sus datos "
                        "de origen o su ruta."
                    ).format(self.name, row.name, period_name))

    def _validate_employer_scope(self):
        if self.status == "Borrador" and not self.employer:
            frappe.throw(_("Seleccione la empresa de esta importación."))
        if self.employer and not frappe.db.exists("CN Employer", self.employer):
            frappe.throw(_("La empresa seleccionada no existe."))
        if self.portfolio_snapshot and self.employer and self.employer != UNIDENTIFIED_EMPLOYER:
            has_company_rows = frappe.get_all(
                "CN Credit Portfolio Row",
                filters={"parent": self.portfolio_snapshot, "employer": self.employer},
                fields=["name"], limit_page_length=1,
            )
            if not has_company_rows:
                frappe.throw(_("El corte de cartera seleccionado no contiene créditos de la empresa indicada."))

    def _validate_historical_periods(self):
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
                    "historical_end_date", "employer",
                ],
                limit_page_length=max(len(selected), 20),
            )
        } if selected else {}
        for name in selected:
            if name not in periods or periods[name].reconciliation_mode != "Historica":
                frappe.throw(_("{0} no es un período histórico.").format(name))
            if self.employer and periods[name].employer != self.employer:
                frappe.throw(_("El período histórico {0} pertenece a otra empresa.").format(name))
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
                "file_hash": self.file_hash,
                "status": ["!=", "Fallido"],
                **({"bulk_source_hash": self.bulk_source_hash} if getattr(self, "bulk_source_hash", None) else {}),
            },
            "name",
        )
        if not duplicate and not getattr(self, "bulk_source_hash", None):
            duplicate = frappe.db.get_value(self.doctype, {
                "name": ["!=", self.name or ""], "bulk_source_hash": self.file_hash,
                "status": ["!=", "Fallido"],
            }, "name")
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
                or row.deposit_match_status in SETTLED_APPLICATION_STATUSES
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
                and row.deposit_match_status not in SETTLED_APPLICATION_STATUSES
            )
            for row in rows
        )
        self.ignored_count = sum(row.match_status == "Ignorado" for row in rows)
        self.total_usd = sum(flt(row.amount_usd) for row in rows if row.effective)
        self.total_nio = sum(flt(row.amount_nio) for row in rows if row.effective)
        self.total_application_adjustment_usd = money_float(sum_money(row.get("application_adjustment_usd") for row in rows if row.effective and row.event_type == "Aplicacion"))
        self.total_net_applied_usd = money_float(sum_money(net_amount(row) for row in rows if row.effective and row.event_type == "Aplicacion"))


@frappe.whitelist()
def get_company_portfolio_snapshots(doctype, txt, searchfield, start, page_len, filters):
    """Return readable portfolio cuts that contain rows for the chosen company."""
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    employer = clean_text(filters.get("employer"))
    if not employer or not frappe.has_permission("CN Credit Portfolio Snapshot", "read"):
        return []
    snapshots = frappe.get_list(
        "CN Credit Portfolio Snapshot",
        filters={"status": ["in", ["Importado", "Importado con alertas"]]},
        fields=["name", "cut_month", "report_date"],
        order_by="report_date desc, name desc", limit_page_length=100000,
    )
    names = [item.name for item in snapshots]
    eligible = {
        row.parent for row in frappe.get_all(
            "CN Credit Portfolio Row",
            filters={"parent": ["in", names], "employer": employer},
            fields=["parent"], limit_page_length=100000,
        )
    } if names else set()
    needle = clean_text(txt).casefold()
    matches = [
        item for item in snapshots
        if item.name in eligible
        and (not needle or needle in clean_text(item.name).casefold()
             or needle in clean_text(item.cut_month).casefold()
             or needle in clean_text(item.report_date).casefold())
    ]
    offset, limit = int(start or 0), int(page_len or 20)
    return [[item.name, f"{item.cut_month or ''} · {item.report_date or ''}"]
            for item in matches[offset:offset + limit]]


def _attached_file(document):
    if not document.source_file:
        frappe.throw(_("Adjunte el archivo de origen."))
    file_doc = frappe.get_doc("File", {"file_url": document.source_file,
                                      "attached_to_doctype": document.doctype,
                                      "attached_to_name": document.name})
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
    document = frappe.get_doc("CN Accounting Import", import_name)
    document.check_permission("write")
    if document.bulk_source_hash:
        document._assert_no_closed_period_links(document.rows or [])
    file_doc, content = _attached_file(document)
    if document.bulk_source_hash and not file_doc.file_name.lower().endswith(".csv"):
        frappe.throw(_("Use el CSV individual de esta importación, no el archivo masivo original."))
    try:
        parsed = parse_accounting_movements(file_doc.file_name, content)
        from credinomina_reconciliation.accounting_identity import identify_lines

        identify_lines(parsed, document.bulk_source_hash or file_sha256(content))
        parsed = apply_accounting_currency_override(
            parsed,
            document.currency,
            document.manual_fx_rate,
        )
        if not document.employer:
            frappe.throw(_("Seleccione la empresa antes de cargar los movimientos contables."))
        if document.bulk_source_hash:
            _validate_bulk_reimport(document, parsed)
        parsed = enrich_accounting_records(
            parsed, document.portfolio_snapshot,
            "" if document.employer == UNIDENTIFIED_EMPLOYER else document.employer,
        )
        parsed = enrich_source_import_clients(parsed)
    except SourceFileError as exc:
        document.status = "Fallido"
        document.notes = str(exc)
        document.save()
        frappe.throw(str(exc), title=_("No se pudo importar la fuente"))

    # Do not reclassify/remove a previously linked application behind the user's back.
    new_application_keys = {row["source_key"] for row in parsed if row["event_type"] == "Aplicacion"}
    for row in document.rows or []:
        if row.event_type == "Aplicacion" and row.source_key not in new_application_keys:
            if (_source_linked_periods(row) or flt(row.historical_remitted_usd)
                    or row.application_allocation_detail not in (None, "", "[]")
                    or frappe.db.exists("CN Complementary Item", {"related_application": row.name})):
                frappe.throw(_("La fila {0} ya tiene vínculos de conciliación. Revise y retire esos vínculos antes de reclasificarla o eliminarla al recargar.").format(row.idx))
    adjustments = [row for row in parsed if row["event_type"] == "Ajuste"]
    if adjustments:
        from credinomina_reconciliation.accounting_review import create_review_items, plan_review_items

        employers = frappe.get_list("CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0)
        attach_employer_aliases(employers)
        review = [dict(row, event_type="Aplicacion") for row in adjustments]
        enrich_accounting_records(review, document.portfolio_snapshot, register_clients=False)
        for row in review:
            row["event_type"] = "Ajuste"
        review = plan_review_items(review, employers, document.employer)
        create_review_items(review, document.source_file, file_sha256(content))
        links = {row["accounting_source_key"]: row["complementary_item"] for row in review}
        for row in adjustments:
            row["complementary_item"] = links[row["accounting_source_key"]]
    from credinomina_reconciliation.accounting_deposits import plan_deposits, create_deposits
    deposits = []
    if any(row.get("accounting_classification") == "Depósito" for row in parsed):
        employers = frappe.get_list("CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0)
        attach_employer_aliases(employers)
        deposits = plan_deposits(parsed, employers, document.employer)
    if deposits:
        create_deposits(deposits, document.source_file, file_sha256(content))
    deposit_links = {row["accounting_source_key"]: row["remittance_allocation"] for row in deposits}
    for row in parsed:
        row["remittance_allocation"] = deposit_links.get(row.get("accounting_source_key"), "")
    existing_settings = defaultdict(list)
    for row in document.rows or []:
        if row.source_key:
            existing_settings[row.source_key].append(
                (
                    row.name, row.manual_fx_rate,
                    row.historical_period,
                    row.processing_route,
                )
            )
    document.file_hash = file_sha256(content)
    document.set("rows", [])
    for record in parsed:
        previous = existing_settings[record["source_key"]]
        preserved_name, manual_rate, prior_period, prior_route = (
            previous.pop(0) if previous else (None, 0, "", "")
        )
        document.append(
            "rows",
            {
                **record,
                **({"name": preserved_name} if preserved_name else {}),
                "manual_fx_rate": record.get("manual_fx_rate", manual_rate),
                "processing_route": prior_route,
                "historical_period": (
                    prior_period or document.historical_period
                    if record["event_type"] == "Aplicacion" else ""
                ),
                "effective": int(record["event_type"] != "Ajuste" and not record.get("remittance_allocation")),
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
    if adjustments:
        document.notes += _(" {0} movimientos no son pagos nuevos: revise sus Partidas complementarias vinculadas; no afectan saldos mientras estén en borrador.").format(len(adjustments))
    if deposits:
        document.notes += _(" {0} depósitos vinculados. Revise sus registros y confirme los borradores; la fila contable no aplica efectivo por separado.").format(len(deposits))
    document.save()
    result = _reconcile_sources(document.employer) if document.bulk_source_hash else reconcile_all_sources()
    result["import_name"] = document.name
    return result


def _validate_bulk_reimport(document, records):
    from credinomina_reconciliation.accounting_batch import group_applications

    # Resolve across all companies first so a file from another company cannot
    # slip through a company-filtered portfolio lookup.
    enrich_accounting_records(records, document.portfolio_snapshot, register_clients=False)
    employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0,
    )
    attach_employer_aliases(employers)
    plan = group_applications(records, employers, document.employer)
    groups = plan["groups"]
    # A single unresolved case may have been manually assigned to a real
    # company during review. Preserve that explicit assignment on CSV reload.
    manual_unknown = len(records) == 1 and len(groups) == 1 and groups[0]["employer"] == UNIDENTIFIED_EMPLOYER
    if (plan["issues"] or plan["excluded"] or len(groups) != 1
            or groups[0]["event_date"] != str(document.bulk_event_date)[:10]
            or (groups[0]["employer"] != document.employer and not manual_unknown)):
        frappe.throw(_("El CSV debe contener únicamente aplicaciones de la empresa {0} y fecha {1}. No se recargó el documento.").format(
            document.employer, document.bulk_event_date,
        ))
    for record in records:
        if document.employer != UNIDENTIFIED_EMPLOYER and not record.get("employer_text") and not record.get("portfolio_employer"):
            record["employer_text"] = document.employer


@frappe.whitelist(methods=["POST"])
def reconcile_all_sources():
    return _reconcile_sources()


@frappe.whitelist(methods=["POST"])
def reconcile_company_sources(import_name: str):
    document = frappe.get_doc("CN Accounting Import", import_name)
    document.check_permission("write")
    employer = clean_text(document.employer)
    if not employer:
        frappe.throw(_("Seleccione y guarde la empresa antes de conciliar."))
    frappe.get_doc("CN Employer", employer).check_permission("read")
    return _reconcile_sources(employer)


def _company_imports(imports, employer):
    """Reject inconsistent boundaries before recomputing any persisted balance."""
    period_employers = {row.name: row.employer for row in frappe.get_all(
        "CN Reconciliation Period", fields=["name", "employer"], limit_page_length=0,
    )}
    employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0,
    )
    attach_employer_aliases(employers)
    employer_resolver = AccountingEmployerResolver(employers)
    selected = []
    for document in imports:
        belongs = document.get("employer") == employer
        related = {period_employers.get(document.historical_period)}
        for row in document.rows:
            related.add(row.get("portfolio_employer") or employer_resolver.resolve(row)[0])
            related.update(period_employers.get(name) for name in _source_linked_periods(row))
        related.discard(None)
        related.discard("")
        if (belongs and related - {employer}) or (not belongs and employer in related):
            frappe.throw(_(
                "La importación {0} tiene datos o vínculos de {1} que no coinciden con su empresa. "
                "Revise la empresa y los períodos de esa importación antes de conciliar."
            ).format(document.name, employer))
        if belongs:
            document.check_permission("write")
            selected.append(document)
    return selected


def _reconciliation_feedback(rows):
    """Disjoint totals and actionable reasons for every uncompleted source row."""
    matched = ignored = 0
    matched_rows = []
    pending_rows = []
    reasons = defaultdict(int)
    for row in rows:
        if row.match_status == "Ignorado":
            ignored += 1
            continue
        complete = (
            row.match_status == "Conciliado"
            and (row.event_type != "Deposito" or flt(row.unallocated_usd) <= CASH_EPSILON)
            and (row.event_type != "Aplicacion" or row.deposit_match_status in SETTLED_APPLICATION_STATUSES)
        )
        if complete:
            matched += 1
            if len(matched_rows) < 100:
                matched_rows.append({
                    "import_name": row._source_import, "row": row.source_row or row.idx,
                    "client_name": row.client_name or "", "loan_number": row.loan_number or "",
                    "reason": (_("Aplicación compensada totalmente por ajustes confirmados; sin depósito recibido.")
                               if row.deposit_match_status == "Aplicación compensada"
                               else _("Aplicación y depósito conciliados.")
                               if row.event_type == "Aplicacion" else _("Movimiento conciliado.")),
                })
            continue
        row_reasons = []
        if row.match_status != "Conciliado":
            row_reasons.append(clean_text(row.match_reason) or _("Aplicación sin coincidencia; revise su identificación y período."))
        if row.event_type == "Aplicacion" and row.deposit_match_status not in SETTLED_APPLICATION_STATUSES:
            row_reasons.append(clean_text(row.deposit_match_reason) or _("Falta conciliar el depósito de esta aplicación."))
        if row.event_type == "Deposito" and flt(row.unallocated_usd) > CASH_EPSILON:
            row_reasons.append(clean_text(row.allocation_reason) or _("Depósito con saldo sin distribuir."))
        reason = " · ".join(dict.fromkeys(row_reasons)) or _("Revise el estado de la fila.")
        reasons[reason] += 1
        pending_rows.append({
            "import_name": row._source_import, "row": row.source_row or row.idx,
            "client_name": row.client_name or "", "loan_number": row.loan_number or "",
            "reason": reason,
        })
    return {
        "matched": matched, "ignored": ignored, "pending": len(pending_rows),
        "pending_rows": pending_rows[:100],
        "matched_rows": matched_rows,
        "pending_reasons": [{"reason": reason, "count": count}
                            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))],
    }


def _reconcile_sources(employer=None):
    if not frappe.has_permission("CN Accounting Import", "write"):
        frappe.throw(_("No tiene permiso para conciliar importaciones."))

    import_names = frappe.get_all(
        "CN Accounting Import",
        filters={"status": ["in", ["Importado", "Importado con excepciones"]]},
        order_by="creation asc",
        pluck="name",
    )
    imports = [frappe.get_doc("CN Accounting Import", name) for name in import_names]
    if employer:
        imports = _company_imports(imports, employer)
    original_source_rows = [
        row.as_dict() for document in imports for row in document.rows
    ]
    all_rows = []
    for document in imports:
        for row in document.rows:
            row._source_import = document.name
            row._source_employer = document.employer
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

    _deduplicate_applications(all_rows)
    refresh_rows(all_rows)
    periods = _load_open_periods(employer) if employer else _load_open_periods()
    company_filters = {"employer": employer} if employer else {}
    closed_operative_state = {
        period.name: _operative_period_state(period)
        for period in periods
        if period.reconciliation_mode != "Historica" and period.status == "Cerrado"
    }
    collection_rows = [row for period in periods for row in period.collection_rows]
    complementary_items = frappe.get_all(
        "CN Complementary Item",
        filters={"docstatus": 1, "category": ["not in", [COMPANY_CREDIT, TOLERANCE_CATEGORY, APPLICATION_ADJUSTMENT, "Compensación entre partidas"]], **company_filters},
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
        filters={"docstatus": 1, **company_filters},
        fields=[
            "name", "deposit_reference", "deposit_voucher", "reconciliation_identity",
            "amount_usd", "result", "employer", "deposit_date",
            "deposit_currency", "deposit_amount", "fx_rate", "notes",
            "allocated_usd", "unallocated_usd",
            "allocation_detail",
            "support_file", "detail_file", "detail_source_file", "detail_hash", "detail_period",
            "detail_status", "detail_total_usd", "detail_count",
        ],
        order_by="creation asc",
    )
    closed_operative_links = _operative_links(
        periods, original_source_rows, manual_allocations, complementary_items,
        closed_operative_state,
    )
    surplus_items = frappe.get_all(
        "CN Complementary Item",
        filters={"docstatus": 1, "category": COMPANY_CREDIT, **company_filters},
        fields=[
            "name", "period", "registered_deposit", "reference as deposit_reference", "deposit_voucher",
            "amount_usd", "result", "employer",
        ],
        order_by="creation asc",
    )
    deposit_pairs, registered_ids = _registered_deposit_pairs(all_rows, manual_allocations)
    _refresh_recognition_evidence(periods, deposit_pairs)
    _match_applications(
        all_rows, collection_rows, deposit_pairs, complementary_by_target, periods
    )
    allocation = _distribute_deposits(
        periods, all_rows, deposit_pairs, complementary_items,
        complementary_by_target, manual_allocations, registered_ids,
    )
    _sync_rounding_movements(allocation["rounding_movements"], allocation, all_rows, employer=employer)
    _classify_surplus(allocation, surplus_items)
    _rebuild_period_balances(
        [period for period in periods if period.reconciliation_mode != "Historica"],
        all_rows, deposit_pairs, allocation,
        closed_operative_state, closed_operative_links, complementary_items,
    )
    _rebuild_historical_balances(periods, all_rows, allocation)
    for row in all_rows:
        if row.event_type == "Aplicacion" and row.effective and row.application_adjustment_usd and net_amount(row) == 0:
            row.match_status = "Conciliado"
            row.deposit_match_status = "Aplicación compensada"
            row.deposit_match_reason = _("Aplicación compensada totalmente por ajustes confirmados; no es un depósito recibido.")
    _sync_registered_deposit_detail(allocation)

    for document in imports:
        document.recalculate_summary()
        document.status = (
            "Importado con excepciones" if document.exception_count else "Importado"
        )
        # The closed-period snapshot was checked above. Only this internal
        # recomputation may refresh the derived child fields after closure.
        token = _source_reconcile_verified.set(True)
        try:
            document.save(ignore_permissions=True)
        finally:
            _source_reconcile_verified.reset(token)

    return {
        "imports": len(imports),
        "rows": len(all_rows),
        "employer": employer,
        "matched": sum(
            row.match_status == "Conciliado"
            and (
                row.event_type != "Deposito"
                or flt(row.unallocated_usd) <= CASH_EPSILON
            )
            and (
                row.event_type != "Aplicacion"
                or row.deposit_match_status in SETTLED_APPLICATION_STATUSES
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
                and row.deposit_match_status not in SETTLED_APPLICATION_STATUSES
            )
            for row in all_rows
        ),
        "ignored": sum(row.match_status == "Ignorado" for row in all_rows),
        **(_reconciliation_feedback(all_rows) if employer else {}),
    }


def _deduplicate_applications(rows):
    # Equal amounts/vouchers are not duplicate evidence. All physical payment
    # lines remain effective; only mirrors of non-payment documents are excluded.
    for row in rows:
        if row.get("remittance_allocation"):
            row.effective = 0
            row.match_status = "Ignorado"
            row.match_reason = _("Depósito registrado en {0}; confirme y concilie desde Distribución de Depósito.").format(row.remittance_allocation)
        if row.event_type == "Ajuste":
            row.effective = 0
            row.match_status = "Ignorado"
            row.match_reason = _(
                "Las dispensas y ajustes no son pagos en efectivo y se revisan por separado."
            )
            if row.get("complementary_item"):
                row.match_reason = _("{0}. Revisar partida {1}; no se contabiliza como aplicación de pago.").format(
                    row.get("classification_reason") or "Movimiento no conciliatorio", row.complementary_item)


def _load_open_periods(employer=None):
    names = frappe.get_all(
        "CN Reconciliation Period",
        filters={"employer": employer} if employer else {},
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
            reason = _("El depósito de respaldo ya no está registrado o disponible.")
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
            period.status = "Pendiente"
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
            return money_float(
                decimal_value(row.expected_usd) * decimal_value(row.deducted_nio)
                / decimal_value(row.expected_nio)
            )
    if currency == "NIO":
        direct = flt(row.deducted_nio)
        if direct > AMOUNT_TOLERANCE:
            return direct
        if flt(row.deducted_usd) and flt(row.expected_usd) and flt(row.expected_nio):
            return money_float(
                decimal_value(row.expected_nio) * decimal_value(row.deducted_usd)
                / decimal_value(row.expected_usd)
            )
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
                 "amount_usd": net_amount(source)}]
    return []


def _match_applications(
    source_rows, collection_rows, deposit_pairs, complementary_by_target, periods
):
    clients = load_client_index()
    clients_by_name = {client["name"]: client for client in clients}
    aliases_by_target = {
        row.name: (clients_by_name.get(row.get("client")) or {}).get("client_aliases", ())
        for row in collection_rows
    }
    collection_values = {row.name: row.as_dict() for row in collection_rows}
    historical_periods = {
        period.name: period for period in periods
        if period.reconciliation_mode == "Historica"
    }
    period_by_name = {period.name: period for period in periods}
    known_employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"],
        limit_page_length=100000,
    )
    attach_employer_aliases(known_employers)
    employer_resolver = AccountingEmployerResolver(known_employers)
    pairs_by_reference = defaultdict(list)
    for account, bank in deposit_pairs:
        pairs_by_reference[clean_text(account.reference)].append((account, bank))
    applied_by_target = defaultdict(float)
    for source in source_rows:
        if source.event_type != "Aplicacion" or not source.effective:
            continue
        if source.get("_source_employer") == UNIDENTIFIED_EMPLOYER:
            source.match_status = "Sin coincidencia"
            source.match_reason = _("Empresa pendiente de identificar. Corrija la empresa de esta importación antes de conciliar.")
            continue
        if source.get("application_adjustment_usd") and net_amount(source) == 0 and not source.historical_period:
            source.match_status = "Conciliado"
            source.match_reason = _("Aplicación compensada totalmente por ajustes confirmados.")
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
        source_employer, _employer_issue = employer_resolver.resolve(source)
        if not source_employer and employer_resolver.resolve_for_import(source)[0] == UNIDENTIFIED_EMPLOYER:
            source_employer = source.get("_source_employer") or ""
        if not source_employer and not any((
            source.loan_number, source.client_number, source.national_id,
        )):
            source.match_status = "Sin coincidencia"
            source.match_reason = _(
                "Falta identificar la empresa para conciliar por nombre o número de empleado."
            )
            continue
        if not any((source.loan_number, source.client_number, source.employee_number, source.national_id)):
            _client, identity_reason = choose_client(source.as_dict(), clients, source_employer)
            if identity_reason.startswith("Nombre ambiguo"):
                source.match_status = "Ambiguo"
                source.match_reason = _(
                    "El nombre corresponde a varios clientes; indique un identificador o corrija sus alias."
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
        application_values = source.as_dict()
        for target in collection_rows:
            target_period = period_by_name.get(target.parent)
            if source_employer and (
                not target_period or target_period.employer != source_employer
            ):
                continue
            if not application_matches_collection(
                application_values, collection_values[target.name],
                aliases_by_target[target.name],
            ):
                continue
            if source.installment_number and canonical_identifier(
                target.installment_number
            ) != canonical_identifier(source.installment_number):
                continue
            if target.deduction_status == "No deducido":
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
                money_float(max(decimal_value(target.deducted_nio) / decimal_value(payment_rate) - decimal_value(complementary), 0))
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
                        "available_usd": money_float(available),
                        "cycle": target_period.collection_cycle,
                        "employer": target_period.employer,
                        "month": str(target_period.payroll_month)[:7],
                        "detail_pending": detail_pending,
                        "converted_match": converted_match,
                    })
            if net_amount(source) > available + AMOUNT_TOLERANCE:
                continue
            exact = same_amount(net_amount(source), available)
            candidates.append((target, converted_match, exact, detail_pending))
        confirmed_candidates = [candidate for candidate in candidates if not candidate[3]]
        if confirmed_candidates:
            candidates = confirmed_candidates
        exact_candidates = [candidate for candidate in candidates if candidate[2]]
        if confirmed_candidates and len(candidates) > 1 and len(exact_candidates) == 1:
            candidates = exact_candidates
        if len(candidates) == 1:
            target, converted_match, _exact, detail_pending = candidates[0]
            applied_by_target[target.name] += net_amount(source)
            source.match_status = (
                PROVISIONAL_APPLICATION if detail_pending else "Conciliado"
            )
            if converted_match:
                source.match_reason = _(
                    "Aplicacion parcial o total en US$ enlazada a la deduccion en C$ con tasa documentada de {0} C$ por US$."
                ).format(payment_rate)
            elif detail_pending:
                source.match_reason = _(
                    "Aplicación enlazada de forma única a la cobranza."
                )
            else:
                source.match_reason = _(
                    "Aplicacion parcial o total enlazada por credito y referencia cuando fue informada; no excede la deduccion disponible."
                )
            if detail_pending:
                source.match_reason += " " + _(
                    "Enlace provisional: falta confirmar la deducción de la empresa; no es conciliación final."
                )
            source.collection_period = target.parent
            source.collection_row_id = target.name
            source.application_allocation_detail = json.dumps([{
                "collection_row_id": target.name,
                "period": target.parent,
                "amount_usd": net_amount(source),
            }], ensure_ascii=False)
        elif not candidates and (
            pair := unique_full_quincena_pair(pair_candidates, net_amount(source))
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
            source.match_status = (
                PROVISIONAL_APPLICATION
                if any(candidate["detail_pending"] for candidate in pair)
                else "Conciliado"
            )
            source.match_reason = _(
                "Una aplicacion del core cubre las dos quincenas del mismo credito, empresa y mes; reparto completo por saldos disponibles."
            )
            if any(candidate["detail_pending"] for candidate in pair):
                source.match_reason += " " + _(
                    "Enlace provisional: falta confirmar la deducción de la empresa; no es conciliación final."
                )
            if any(candidate["converted_match"] for candidate in pair):
                source.match_reason += " " + _(
                    "Conversion con tasa documentada de {0} C$ por US$."
                ).format(payment_rate)
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


def _registered_deposit_pairs(rows, allocations):
    """Use registered deposits as cash evidence."""
    registered = list(allocations)
    if not registered:
        return [], {}
    accounting = [
        row for row in rows
        if row.event_type == "Deposito" and row.effective
    ]
    used_account_ids = set()
    pairs = []
    deposit_ids = {}
    for item in registered:
        fx_basis = remittance_fx_basis(item) if item.deposit_currency == "NIO" else "Moneda original USD"
        registered_row = _RegisteredDeposit(
            name=item.name,
            reconciliation_identity=getattr(item, "reconciliation_identity", None) or item.name,
            reference=clean_text(item.deposit_reference),
            voucher=clean_text(item.deposit_voucher),
            event_date=item.deposit_date,
            currency=item.deposit_currency,
            amount=flt(item.deposit_amount),
            equivalent_currency="USD",
            equivalent_amount=flt(item.amount_usd),
            fx_basis=fx_basis,
            fx_rate=flt(item.fx_rate),
            manual_fx_rate=flt(item.fx_rate),
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
            account.match_reason = _("Movimiento contable cotejado con el depósito registrado.")
            pairs.append((registered_row, account))
            deposit_ids[item.name] = item.name
        else:
            # A real deposit may arrive after the core application and need not
            # have an imported accounting deposit row at registration time.
            pairs.append((registered_row, registered_row))
            deposit_ids[item.name] = item.name
    return pairs, deposit_ids


def _prepare_remittance_details(
    remittances, registered_ids, deposits, claims, prior_instructions,
    tolerance_by_employer,
):
    """Reserve a deposit for its client detail instead of guessing a split."""
    from credinomina_reconciliation.remittance_detail import manual_detail_targets
    by_deposit = {deposit["id"]: deposit for deposit in deposits}
    names = [item.name for item in remittances]
    detail_rows = frappe.get_all(
        "CN Remittance Detail",
        filters={"parent": ["in", names]},
        fields=[
            "name", "parent", "source_row", "row_key", "client", "identity_reason", "client_name", "client_number", "employee_number",
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
        deposit_id = registered_ids.get(item.name)
        if deposit_id not in by_deposit:
            continue
        deposit = by_deposit[deposit_id]
        rows = rows_by_parent[item.name]
        attached_detail = bool(item.detail_file or item.detail_hash)
        # A registered deposit is evidence of cash received, not of its
        # per-client split. Explicit targets (including a documented
        # collection-as-detail recognition) remain valid without a file.
        if not attached_detail:
            blocked.add(deposit_id)
            explicit_amount = sum(
                flt(entry["amount_usd"]) for entry in prior_instructions
                if entry["deposit_id"] == deposit_id
            )
            contexts[item.name] = {
                "status": (
                    "Distribución manual"
                    if explicit_amount >= flt(deposit["amount_usd"]) - CASH_EPSILON
                    else "Detalle pendiente"
                ),
                "rows": [],
            }
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
        rate = flt(item.fx_rate) if remittance_fx_basis(item) else 0
        for row in rows:
            amount, explanation = detail_amount_usd(row, rate)
            total_usd = money_float(total_usd + amount)
            plans.append({
                "row": row, "amount_usd": amount, "explanation": explanation,
                "targets": [], "status": "", "reason": "",
            })
        # Administrative collections and other signed complements belong to
        # targets, not to fictitious client detail rows. Linked complements
        # are already included in their row's net; count only separate ones.
        outside_complements = [
            entry for entry in prior_instructions
            if entry["deposit_id"] == deposit_id and not entry.get("detail_row")
            and entry["claim_id"].startswith("X:")
        ]
        covered_total = money_float(money(total_usd) + sum_money(
            entry["amount_usd"] for entry in outside_complements
        ))
        excessive = covered_total > flt(deposit["amount_usd"]) + CASH_EPSILON
        one_to_one_candidate = (
            len([plan for plan in plans if plan["amount_usd"] > CASH_EPSILON]) == 1
            and abs(total_usd - flt(deposit["amount_usd"])) <= CASH_EPSILON
            and deposit.get("currency") == "USD"
            and deposit.get("bank_currency") == "USD"
            and not any(entry["deposit_id"] == deposit_id for entry in prior_instructions)
        )
        for plan in plans:
            amount = plan["amount_usd"]
            manual = [entry for entry in prior_instructions
                      if entry["deposit_id"] == deposit_id
                      and entry.get("detail_row") == plan["row"].name]
            if clean_text(plan["row"].identity_reason).startswith(("Conflicto", "Nombre ambiguo")):
                plan["status"] = "Revisar"
                plan["reason"] = plan["row"].identity_reason
                continue
            if not amount:
                plan["status"] = (
                    "No deducido" if plan["explanation"] == "No deducido" and not manual
                    else "Revisar"
                )
                plan["reason"] = ("Hay destinos manuales vinculados a una fila sin importe válido"
                                  if manual else plan["explanation"])
                continue
            if excessive:
                plan["status"] = "Revisar"
                plan["reason"] = "La suma del detalle supera el depósito"
                continue
            if manual:
                plan["targets"], plan["reason"] = manual_detail_targets(
                    plan["row"], claims, manual, amount, item.employer, item.detail_period or "",
                )
                if not plan["targets"]:
                    plan["status"] = "Revisar"
                # These instructions already exist. Only associate their results.
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
            "rows": plans, "total_usd": total_usd, "covered_total_usd": covered_total,
            "outside_complements": outside_complements,
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


def _sync_remittance_details(context, allocation, claims=()):
    from credinomina_reconciliation.remittance_target_summary import describe_targets, load_target_descriptions

    descriptions = load_target_descriptions([
        target for state in context["contexts"].values()
        for plan in state["rows"] for target in plan["targets"]
    ], claims)
    statuses = {}
    for parent, state in context["contexts"].items():
        if state["rows"]:
            for plan in state["rows"]:
                targets = plan["targets"]
                if targets:
                    results = [
                        allocation["instruction_results"].get(
                            target.get("instruction_id") or "D:" + plan["row"].name + ":" + target["claim_id"],
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
                target_loans = {
                    clean_text(descriptions.get(target.get("claim_id"), {}).get("loan_number"))
                    for target in targets
                    if clean_text(descriptions.get(target.get("claim_id"), {}).get("loan_number"))
                }
                unidentified_application = any(
                    clean_text(target.get("claim_id")).partition(":")[0] in {"H", "C"}
                    and not clean_text(descriptions.get(target.get("claim_id"), {}).get("loan_number"))
                    for target in targets
                )
                updates = {
                    "amount_usd": plan["amount_usd"],
                    "match_status": plan["status"],
                    "match_reason": plan["reason"],
                    "matched_targets": json.dumps(targets, ensure_ascii=False),
                    "matched_targets_summary": describe_targets(targets, descriptions),
                }
                if len(target_loans) == 1 and not unidentified_application:
                    updates["loan_number"] = next(iter(target_loans))
                frappe.db.set_value(
                    "CN Remittance Detail", plan["row"].name,
                    updates,
                    update_modified=False,
                )
            if not state["status"]:
                outside_complements = state.get("outside_complements", [])
                # Planned targets are not evidence of settlement: a draft,
                # cancelled, exhausted or otherwise rejected complement must
                # never make the detail ready for period closure.
                outside_applied = all(
                    allocation["instruction_results"].get(entry["id"]) == "Aplicada"
                    for entry in outside_complements
                )
                covered_total = money(state["total_usd"]) + sum_money(
                    entry["amount_usd"] for entry in outside_complements
                    if allocation["instruction_results"].get(entry["id"]) == "Aplicada"
                )
                if any(
                    plan["status"] not in {"Conciliada", "No deducido"}
                    for plan in state["rows"]
                ) or not outside_applied:
                    state["status"] = "Revisar filas"
                elif covered_total < money(state["deposit_usd"]) - decimal_value(CASH_EPSILON):
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
    attach_employer_aliases(known_employers)
    tolerance_by_employer = {
        employer.name: money_float(employer.rounding_tolerance_usd)
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
                "Falta tipo de cambio para distribuir el deposito."
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
                "Los equivalentes US$ del depósito y el movimiento contable difieren."
            )
            for source in (account, bank):
                source.match_status = "Sin coincidencia"
                source.match_reason = source.allocation_reason
            continue
        amount_usd = money_float(account_usd if account_usd is not None else bank_usd)
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
             "reconciliation_identity": account.get("reconciliation_identity") or account.name,
             "amount_usd": amount_usd,
             "currency": account.currency, "bank_currency": bank.currency,
             "bank_amount_usd": flt(bank.amount) if bank.currency == "USD" else 0,
             "group": employer}
        )
        deposit_meta[account.name] = {
            "account": account, "bank": bank,
            "employer": employer,
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
            and source.match_status in LINKED_APPLICATION_STATUSES
        ):
            reference = clean_text(source.reference)
            for link in _application_allocations(source):
                row_id = link["collection_row_id"]
                core_amount_by_row[row_id] += flt(link["amount_usd"])
                application_ids_by_row[row_id].add(source.name)
                if reference:
                    references_by_row[row_id].add(reference)
                    hints_by_row[row_id][reference] += flt(link["amount_usd"])

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
             "client": row.get("client"), "client_name": row.client_name,
             "client_number": row.client_number, "employee_number": row.employee_number,
             "national_id": row.national_id,
             "loan_number": row.loan_number,
             "installment_number": row.installment_number,
             "references": references, "hints": dict(hints_by_row[row.name]),
             "group": employer_by_period.get(row.parent),
             "period": row.parent,
             "core_applied_usd": money_float(core_amount_by_row[row.name]),
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
        claims.append(
            {
                "id": "H:" + application.name,
                "amount_usd": net_amount(application),
                "kind": "H", "client_number": application.client_number,
                "client": application.client or application.portfolio_client,
                "employee_number": application.employee_number,
                "national_id": application.national_id,
                "client_name": application.client_name,
                "loan_number": application.loan_number,
                "installment_number": application.installment_number,
                "references": [clean_text(application.reference)],
                "hints": {},
                "group": employer_by_period.get(application.historical_period),
                "period": application.historical_period,
                "core_applied_usd": net_amount(application),
                "application_ids": [application.name],
            }
        )
    client_catalog = load_client_index()
    for claim in claims:
        claim["client_names"] = names_for_claim(claim, client_catalog, claim.get("group"))
        client, reason = choose_client(claim, client_catalog, claim.get("group"))
        if client and reason in {"Identificador exacto", "Nombre o alias único"}:
            claim["client"] = client["name"]
            claim["client_number"] = claim.get("client_number") or client["client_number"]
            claim["employee_number"] = claim.get("employee_number") or client["employee_number"]
            claim["national_id"] = claim.get("national_id") or client["national_id"]

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
    registered_names = [item.name for item in manual_allocations]
    target_rows = frappe.get_all(
        "CN Remittance Target",
        filters={"parent": ["in", registered_names]},
        fields=[
            "name", "parent", "period", "row_key", "historical_application",
            "complementary_item", "amount_usd", "result", "detail_row",
        ],
        order_by="parent asc, idx asc",
        limit_page_length=100000,
    ) if registered_names else []
    allocation_instructions = [
        (item, target) for item in manual_allocations
        for target in target_rows if target.parent == item.name
    ]
    for parent, item in allocation_instructions:
        candidates = [
            deposit for deposit in deposits
            if deposit["id"] == registered_ids.get(parent.name)
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
             "claim_id": claim_id, "amount_usd": flt(item.amount_usd),
             "detail_row": item.get("detail_row")}
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
    detail_statuses = _sync_remittance_details(detail_context, result, claims)
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
        result["deposit_remaining"][deposit_id] = money_float(
            result["deposit_remaining"][deposit_id]
            - movement["consumed_residual_usd"]
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
        deposit_id = registered_ids[item.name]
        allocated = money_float(
            flt(item.amount_usd) - flt(result["deposit_remaining"].get(deposit_id, item.amount_usd))
        )
        remaining = money_float(flt(item.amount_usd) - allocated)
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
        assigned = money_float(assigned_by_deposit[deposit_id])
        remaining = money_float(result["deposit_remaining"][deposit_id])
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
            company = meta_by_id[deposit_id].get("employer")
            if item.get("employer") and company and item.employer != company:
                status = "Empresa no coincide"
            elif not can_document_surplus(
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
                "CN Complementary Item", item.name, "result", status,
                update_modified=False,
            )
    for deposit_id, meta in meta_by_id.items():
        total = flt(allocation["deposit_remaining"][deposit_id])
        company_credit = money_float(justified[deposit_id])
        unclassified = money_float(max(total - company_credit, 0))
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
        detail_status = frappe.db.get_value(
            "CN Remittance Allocation", name, "detail_status"
        )
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
        if (
            detail_status == "Parcial; saldo sin detalle"
            and flt(source.unallocated_usd) > CASH_EPSILON
            and flt(source.justified_surplus_usd) > CASH_EPSILON
            and flt(source.unclassified_usd) <= CASH_EPSILON
            and abs(flt(source.unallocated_usd) - flt(source.justified_surplus_usd))
            <= CASH_EPSILON
        ):
            detail_status = "Conciliado; excedente documentado"
        elif (
            detail_status == "Detalle pendiente"
            and current_result == "Detalle pendiente"
            and flt(source.allocated_usd) > CASH_EPSILON
            and flt(source.unallocated_usd) > CASH_EPSILON
            and flt(source.justified_surplus_usd) > CASH_EPSILON
            and flt(source.unclassified_usd) <= CASH_EPSILON
            and abs(flt(source.unallocated_usd) - flt(source.justified_surplus_usd))
            <= CASH_EPSILON
            and not frappe.db.get_value("CN Remittance Allocation", name, "detail_file")
            and not frappe.db.get_value("CN Remittance Allocation", name, "detail_hash")
        ):
            # Explicit targets (or recognized collection) document the loan
            # portion; the submitted surplus explains the remaining cash.
            detail_status = "Distribución manual; excedente documentado"
            result = "Parcial con saldo a favor"
        frappe.db.set_value(
            "CN Remittance Allocation", name,
            {
                "result": result,
                "detail_status": detail_status,
                "allocation_detail": source.allocation_detail,
                "inherited_exception_comment": getattr(source, "inherited_exception_comment", ""),
                "justified_surplus_usd": flt(source.justified_surplus_usd),
                "unclassified_usd": flt(source.unclassified_usd),
            },
            update_modified=False,
        )


def _sync_rounding_movements(movements, allocation, source_rows, employer=None):
    """Persist deterministic movements and reverse stale ones; never post GL."""
    desired = {movement["name"]: movement for movement in movements}
    existing = {
        item.name: item
        for item in frappe.get_all(
            "CN Complementary Item",
            filters={"category": TOLERANCE_CATEGORY, "docstatus": 1, **({"employer": employer} if employer else {})},
            fields=["name", "status", "period"],
            limit_page_length=100000,
        )
    }
    for name, item in existing.items():
        if item.status != "Vigente" or name in desired:
            continue
        if frappe.db.get_value("CN Reconciliation Period", item.period, "status") == "Cerrado":
            frappe.throw(_("El movimiento {0} pertenece a un período cerrado y no puede revertirse automáticamente.").format(name))
        document = frappe.get_doc("CN Complementary Item", name)
        document.status = "Revertido"
        document.reversed_on = now_datetime()
        document.reversal_reason = _(
            "La diferencia ya no cumple la tolerancia, la referencia o el enlace único entre aplicación y depósito."
        )
        with tolerance_item_write():
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
                document = frappe.get_doc("CN Complementary Item", name)
                document.status = "Vigente"
                document.reversed_on = None
                document.reversal_reason = ""
                with tolerance_item_write():
                    document.save(ignore_permissions=True)
            else:
                deposit = allocation["deposit_meta"][movement["deposit_id"]]["account"]
                with tolerance_item_write():
                    document = frappe.get_doc(item_values(movement, deposit))
                    document.insert(ignore_permissions=True, set_name=name)
                    document.submit()
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
            source.rounding_adjustment_usd = money_float(
                sum(flt(entry["diferencia_usd"]) for entry in entries)
            )
            source.rounding_movement_detail = json.dumps(entries, ensure_ascii=False)


_OPERATIVE_PERIOD_AMOUNTS = (
    "expected_usd", "expected_nio", "deducted_usd", "deducted_nio",
    "applied_usd", "applied_nio", "complementary_usd", "remitted_usd",
    "remitted_nio", "fx_variance_usd", "rounding_adjustment_usd",
)
_OPERATIVE_ROW_AMOUNTS = _OPERATIVE_PERIOD_AMOUNTS
_OPERATIVE_ROW_STATES = (
    "deduction_currency", "deduction_status", "deduction_evidence_date",
    "deduction_match_note", "application_status", "remittance_detail",
    "inherited_exception_comment",
)


def _canonical_detail(value):
    """Treat JSON order and harmless numeric formatting as immaterial."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (TypeError, ValueError):
            return value
    if isinstance(value, dict):
        return tuple(sorted((key, _canonical_detail(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(sorted((_canonical_detail(item) for item in value), key=repr))
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return money_float(value)
    return value


def _detail_entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _operative_period_state(period):
    """Snapshot persisted balances and per-client evidence before recomputation."""
    rows = []
    for row in period.collection_rows:
        rows.append((
            row.name,
            tuple(money_float(row.get(field)) for field in _OPERATIVE_ROW_AMOUNTS),
            tuple(
                _canonical_detail(row.get(field)) if field == "remittance_detail"
                else str(row.get(field) or "")
                for field in _OPERATIVE_ROW_STATES
            ),
        ))
    return (
        period.status,
        tuple(money_float(period.get(field)) for field in _OPERATIVE_PERIOD_AMOUNTS),
        int(period.exception_count or 0),
        period.deduction_basis or "",
        period.deduction_recognition_deposit or "",
        period.deduction_recognition_reference or "",
        tuple(sorted(rows, key=lambda row: row[0])),
    )


def _operative_links(periods, source_rows, registered_deposits, complementary_items, closed_state):
    """Identify application and cash edges, including their source document IDs.

    Totals alone cannot reveal a deposit or application replaced by a different
    one for the same amount. These edges protect that audit trail after closure.
    """
    closed_names = set(closed_state)
    row_period = {
        row.name: period.name
        for period in periods if period.name in closed_names
        for row in period.collection_rows
    }
    complementary_period = {
        item.name: item.period for item in complementary_items
        if item.period in closed_names
    }
    links = defaultdict(list)
    for source in source_rows:
        if source.get("event_type") == "Aplicacion":
            details = _detail_entries(source.get("application_allocation_detail"))
            if not details and source.get("collection_row_id"):
                details = [{
                    "collection_row_id": source.get("collection_row_id"),
                    "amount_usd": source.get("amount"),
                }]
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                row_id = detail.get("collection_row_id")
                period_name = row_period.get(row_id)
                if period_name:
                    links[period_name].append((
                        "Aplicacion", source.get("name"), row_id,
                        money_float(detail.get("amount_usd")),
                        clean_text(source.get("reference")),
                        clean_text(source.get("voucher")),
                        clean_text(source.get("accounting_entry")),
                        clean_text(source.get("receipt")),
                        str(source.get("event_date") or ""),
                    ))
    registered_names = {item.name for item in registered_deposits}
    for source in list(source_rows) + list(registered_deposits):
        if source.get("event_type") != "Deposito" and source.get("name") not in registered_names:
            continue
        for detail in _detail_entries(source.get("allocation_detail")):
            if not isinstance(detail, dict):
                continue
            period_name = detail.get("periodo")
            if detail.get("tipo") == "Partida complementaria":
                period_name = complementary_period.get(detail.get("partida"))
            if period_name in closed_names:
                links[period_name].append((
                    "Deposito", source.get("name"), _canonical_detail(detail),
                    clean_text(source.get("reference") or source.get("deposit_reference")),
                    clean_text(source.get("voucher") or source.get("deposit_voucher")),
                    str(source.get("event_date") or source.get("deposit_date") or ""),
                    clean_text(source.get("currency") or source.get("deposit_currency")),
                    money_float(source.get("amount") or source.get("deposit_amount")),
                    round(flt(source.get("fx_rate")), 8),
                ))
    return {
        name: tuple(sorted(links[name], key=repr)) for name in closed_names
    }


def _operative_period_fully_reconciled(period):
    """A paid subset must not clear the entire payroll collection."""
    rows = list(period.collection_rows or [])
    return bool(rows) and not period.exception_count and all(
        row.deduction_status in {"Deduccion total", "Inferida por depósito"}
        and row.application_status == "Aplicado y remitido"
        for row in rows
    )


def _operative_period_status(period, has_unclassified_surplus=False):
    if has_unclassified_surplus:
        return "Con excedente"
    if _operative_period_fully_reconciled(period):
        return "Conciliado"
    if any(flt(row.remitted_usd) > CASH_EPSILON for row in period.collection_rows or []):
        return "Parcial"
    if not period.collection_rows and period.status == "Borrador":
        return "Borrador"
    return "Pendiente"


def _rebuild_period_balances(
    periods, source_rows, deposit_pairs, allocation,
    closed_state, closed_links, complementary_items,
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
            or source.match_status not in LINKED_APPLICATION_STATUSES
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
        target.remitted_nio = money_float(
            decimal_value(target.remitted_nio)
            + decimal_value(amount) * decimal_value(deposit_rate or collection_rate)
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
        target.remitted_nio = money_float(
            decimal_value(target.remitted_nio) + decimal_value(consumed) * decimal_value(rate)
        )
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
            variance = money_float(deducted_usd - flt(target.remitted_usd))
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
            and target.deduction_status not in {"", "Pendiente de detalle"}
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
            target.application_status = "Depósito parcial"
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
        if source.match_status == PROVISIONAL_APPLICATION:
            source.deposit_match_status = (
                "Depósito parcial"
                if any(flt(target.remitted_usd) > AMOUNT_TOLERANCE for target in targets)
                else "Pendiente"
            )
            source.deposit_match_reason = _(
                "La aplicación está enlazada provisionalmente; confirme el detalle de deducción de la empresa antes de conciliar el depósito."
            )
            source.fx_variance_usd = sum(flt(target.fx_variance_usd) for target in targets)
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
            source.deposit_match_status = "Depósito conciliado"
        elif any(flt(target.remitted_usd) > AMOUNT_TOLERANCE for target in targets):
            source.deposit_match_status = "Depósito parcial"
        else:
            source.deposit_match_status = "Sin deposito"
        source.deposit_match_reason = _(
            "{0} cobranza(s): {1} US$ deducidos, {2} US$ remitidos; {3} US$ aplicados al credito; ajuste de conciliación {4} US$."
        ).format(
            len(targets),
            money_float(sum_money(_deducted_amount(target, "USD") for target in targets)),
            money_float(sum_money(target.remitted_usd for target in targets)),
            money_float(sum_money(target.applied_usd for target in targets)),
            money_float(sum_money(target.rounding_adjustment_usd for target in targets)),
        )
        source.fx_variance_usd = sum(flt(target.fx_variance_usd) for target in targets)

    excess_periods = set()
    for entry in allocation["allocations"]:
        if not entry["claim_id"].startswith("C:"):
            continue
        target = rows_by_name.get(entry["claim_id"][2:])
        account = allocation["deposit_meta"][entry["deposit_id"]]["account"]
        if target and flt(account.unclassified_usd) > CASH_EPSILON:
            excess_periods.add(target.parent)
    for period in periods:
        period.recalculate_totals()
        if period.status != "Cerrado":
            period.status = _operative_period_status(period, period.name in excess_periods)
    if closed_state:
        registered_rows = [
            allocation["deposit_meta"][deposit_id]["account"]
            for deposit_id in allocation["registered_ids"].values()
            if deposit_id in allocation["deposit_meta"]
        ]
        current_links = _operative_links(
            periods, source_rows, registered_rows, complementary_items, closed_state,
        )
        for period in periods:
            if period.name not in closed_state:
                continue
            if (
                _operative_period_state(period) != closed_state[period.name]
                or current_links[period.name] != closed_links[period.name]
            ):
                frappe.throw(_(
                    "El período operativo {0} está cerrado y esta conciliación cambiaría "
                    "sus saldos, estados o vínculos de aplicación y depósito. "
                    "Use Reabrir período antes de recalcularlo."
                ).format(period.name))

    for period in periods:
        if period.status != "Cerrado":
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
        remitted = money_float(sum_money(item["importe_usd"] for item in details))
        applied = net_amount(application)
        adjustment = money_float(rounding_by_application[application.name])
        application.historical_remitted_usd = remitted
        application.historical_balance_usd = historical_balance(applied + adjustment, remitted)
        application.historical_detail = json.dumps(details, ensure_ascii=False)
        application.deposit_match_status = (
            "Depósito conciliado" if application.historical_balance_usd <= CASH_EPSILON
            else "Depósito parcial" if remitted > CASH_EPSILON
            else "Sin deposito"
        )
        application.deposit_match_reason = _(
            "Histórico: {0} US$ aplicados al crédito; ajuste {1} US$; {2} US$ vinculados a depósitos; {3} US$ pendientes de evidencia de depósito."
        ).format(
            money_float(applied), adjustment, remitted, application.historical_balance_usd,
        )
        apps_by_period[application.historical_period].append(application)

    for period in historical_periods.values():
        related = apps_by_period[period.name]
        applied = money_float(sum_money(net_amount(row) for row in related))
        remitted = money_float(sum_money(row.historical_remitted_usd for row in related))
        adjustment = money_float(sum_money(rounding_by_application[row.name] for row in related))
        fingerprint_data = [
            {
                "application_id": row.name,
                "amount_usd": net_amount(row),
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
            row.deposit_match_status not in SETTLED_APPLICATION_STATUSES for row in related
        ) + len(unclassified_deposits_by_period[period.name])
        if period.status != "Cerrado":
            period.status = (
                "Con excedente"
                if unclassified_deposits_by_period[period.name]
                else "Conciliado" if related and applied == 0 and all(row.get("application_adjustment_usd") for row in related)
                else historical_status(applied + adjustment, remitted)
            )
        if period.status == "Cerrado":
            with period_write_action("reconcile"):
                period.save(ignore_permissions=True)
        else:
            period.save(ignore_permissions=True)


def _equivalent_amount(target, source, amount_usd):
    if (
        source.currency == "USD"
        and flt(target.expected_usd) > AMOUNT_TOLERANCE
        and flt(target.expected_nio) > AMOUNT_TOLERANCE
    ):
        return "NIO", money_float(
            decimal_value(amount_usd) * decimal_value(target.expected_nio)
            / decimal_value(target.expected_usd)
        )
    if (
        source.currency == "NIO"
        and flt(target.expected_nio) > AMOUNT_TOLERANCE
        and flt(target.expected_usd) > AMOUNT_TOLERANCE
    ):
        return "USD", money_float(
            decimal_value(amount_usd) * decimal_value(target.expected_usd)
            / decimal_value(target.expected_nio)
        )
    return None, 0
