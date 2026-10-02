"""Repair CSV origin metadata without replaying any financial operation."""
from collections import defaultdict, deque
import json

import frappe
from frappe.utils import cint, escape_html, now_datetime

from credinomina_reconciliation.accounting_evidence import evidence_signature, same_ledger_evidence
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.parsers import (
    SourceFileError, apply_accounting_currency_override, file_sha256, parse_accounting_movements,
)

IMPORT = "CN Accounting Import"
SOURCE = "CN Source Row"
REPAIR_FIELDS = ("source_row", "accounting_source_key", "remittance_allocation", "complementary_item")


def plan_origin_repairs(document, records):
    """Exact one-to-one pairing, including independent identical physical lines.

    Keep names, financial statuses, closed-period links, deposit targets and all
    amounts. A mismatch rejects the whole document, never half of its rows.
    """
    if len(document.rows) != len(records):
        raise SourceFileError("La cantidad de filas guardadas no coincide con el CSV; requiere revisión manual.")
    saved = defaultdict(deque)
    healthy = defaultdict(deque)
    for row in sorted(document.rows, key=lambda value: value.idx):
        identity = (row.event_type, evidence_signature(row))
        saved[identity].append(row)
        healthy[(*identity, row.source_row, row.accounting_source_key)].append(row)
    paired, used = {}, set()
    # A reordered grid must not swap already-correct identical physical lines.
    for index, record in enumerate(records):
        identity = (record["event_type"], evidence_signature(record))
        candidates = healthy[(*identity, record["source_row"], record["accounting_source_key"])]
        if candidates:
            row = candidates.popleft()
            paired[index] = row
            used.add(row.name)
    changes = []
    for index, record in enumerate(records):
        row = paired.get(index)
        if row is None:
            candidates = saved.get((record["event_type"], evidence_signature(record)))
            while candidates and candidates[0].name in used:
                candidates.popleft()
            if not candidates:
                raise SourceFileError(f"Fila original {record['source_row']}: la evidencia guardada difiere del CSV; no se modificó la importación.")
            row = candidates.popleft()
            used.add(row.name)
        desired = {"source_row": record["source_row"], "accounting_source_key": record["accounting_source_key"]}
        # Bulk CSVs contain applications only. These fields represent original
        # evidence mirrors, NOT deposits allocated to or adjustments of a loan.
        if record["event_type"] == "Aplicacion":
            desired.update(remittance_allocation="", complementary_item="")
        updates = {field: value for field, value in desired.items()
                   if (cint(row.get(field)) if field == "source_row" else row.get(field) or "") != value}
        if updates:
            changes.append({"row": row.name, "before": {field: row.get(field) for field in updates}, "after": updates})
    return changes


def _read_file(url):
    if not url:
        raise SourceFileError("Falta el archivo de origen.")
    file_doc = frappe.get_doc("File", {"file_url": url})
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    return file_doc, content


def _csv_records(document, original):
    file_doc, content = _read_file(document.source_file)
    if not file_doc.file_name.lower().endswith(".csv"):
        raise SourceFileError("El archivo individual no es CSV.")
    if not document.file_hash or file_sha256(content) != document.file_hash:
        raise SourceFileError("El CSV fue cambiado después de importarlo; no se reparó automáticamente.")
    records = apply_accounting_currency_override(
        parse_accounting_movements(file_doc.file_name, content), document.currency, document.manual_fx_rate,
    )
    assert_csv_origins(document, records, original)
    return identify_lines(records, document.bulk_source_hash)


def assert_csv_origins(document, records, original=None):
    """Validate a replaced CSV before identity reuse or client/financial writes."""
    if original is None:
        original = _original_records([document])
    for record in records:
        origin = original.get(record["source_row"])
        if (not record.get("_csv_original_row") or record["event_type"] != "Aplicacion" or not origin
                or record["accounting_source_key"] != origin["accounting_source_key"]
                or not same_ledger_evidence(record, {**origin, "source_currency": document.currency})):
            raise SourceFileError(f"Fila {record['source_row']}: el CSV no acredita la misma aplicación del archivo masivo original.")


def _original_records(parents):
    """Try another attachment of the same hash if one parent lost its file."""
    error = "Falta el archivo masivo original para acreditar las filas del CSV."
    for url in dict.fromkeys(parent.bulk_source_file for parent in parents if parent.bulk_source_file):
        try:
            file_doc, content = _read_file(url)
            if file_sha256(content) != parents[0].bulk_source_hash:
                raise SourceFileError("La huella del archivo masivo original no coincide con la importación.")
            return {row["source_row"]: row for row in parse_accounting_movements(file_doc.file_name, content)}
        except (SourceFileError, frappe.DoesNotExistError, FileNotFoundError) as exc:
            error = str(exc)
    raise SourceFileError(error)


def repair_accounting_origins(dry_run=True, import_names=None):
    """Bench/migration entry point, intentionally not exposed as a web endpoint.

    Work one original file at a time to bound memory. Do not commit here: the
    migration/console transaction owns the repair and its audit comments.
    """
    dry_run = bool(cint(dry_run))
    filters = {"bulk_source_hash": ["is", "set"]}
    if import_names is not None:
        if not import_names:
            return {"dry_run": dry_run, "imports_checked": 0, "imports_changed": 0, "rows_changed": 0,
                    "affected_imports": [], "issues": []}
        filters["name"] = ["in", import_names]
    parents = frappe.get_all(IMPORT, filters=filters, fields=["name", "employer", "bulk_source_hash", "bulk_source_file"],
                             order_by="bulk_source_hash, name", limit_page_length=0)
    result = {"dry_run": dry_run, "imports_checked": 0, "imports_changed": 0, "rows_changed": 0,
              "affected_imports": [], "issues": []}
    if not dry_run and parents:
        # Use the same transaction locks as scoped reconciliation/bulk creation
        # so no worker writes back stale origins after this repair.
        from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
        from credinomina_reconciliation.accounting_batch_store import ensure_mutex, lock_creation
        lock_cash_pool(sorted({parent.employer for parent in parents if parent.employer}))
        ensure_mutex()
        lock_creation()
    by_hash = defaultdict(list)
    for parent in parents:
        by_hash[parent.bulk_source_hash].append(parent)
    original, current_hash, original_error = {}, None, ""
    for parent in parents:
        result["imports_checked"] += 1
        document = frappe.get_doc(IMPORT, parent.name)
        try:
            if parent.bulk_source_hash != current_hash:
                current_hash, original, original_error = parent.bulk_source_hash, {}, ""
                try:
                    original = _original_records(by_hash[current_hash])
                except (SourceFileError, frappe.DoesNotExistError, FileNotFoundError) as exc:
                    original_error = str(exc)
            if original_error:
                raise SourceFileError(original_error)
            records = _csv_records(document, original)
            changes = plan_origin_repairs(document, records)
        except (SourceFileError, frappe.DoesNotExistError, FileNotFoundError) as exc:
            result["issues"].append({"import": parent.name, "reason": str(exc)})
            continue
        if not changes:
            continue
        result["imports_changed"] += 1
        result["rows_changed"] += len(changes)
        result["affected_imports"].append({"import": document.name, "rows": len(changes)})
        if dry_run:
            continue
        for change in changes:
            frappe.db.set_value(SOURCE, change["row"], change["after"], update_modified=False)
        # Full before/after metadata is recoverable in the document timeline.
        document.add_comment("Info", "Reparación de identidad contable y fila original del CSV. "
            "No se modificaron importes ni conciliaciones.<pre>"
            + escape_html(json.dumps(changes, ensure_ascii=False, indent=2, default=str)) + "</pre>")
        frappe.db.set_value(IMPORT, document.name, "modified", now_datetime(), update_modified=False)
        frappe.clear_document_cache(IMPORT, document.name)
    return result
