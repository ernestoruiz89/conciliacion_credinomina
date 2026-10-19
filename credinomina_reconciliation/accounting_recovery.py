"""Rebuild an importable CSV from saved accounting evidence, without writing it back."""

import csv
import io
import re

import frappe
from frappe import _

from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER
from credinomina_reconciliation.parsers import (
    SourceFileError,
    apply_accounting_currency_override,
    clean_text,
    parse_accounting_movements,
)
from credinomina_reconciliation.rounding import money


COLUMNS = (
    "FECHA_APLICA", "CUENTA_CONTABLE", "DESCRIPCION_CTA_CONTABLE",
    "NO_CMPTE", "NO_REF", "DESCRIPCION", "DEBITO_DEL_MES",
    "CREDITO_DEL_MES", "TMOV", "TDOC", "CLASIFICACION",
    "NOMBRE_CLIENTE", "NO_CREDITO", "EMPRESA", "CN_FILA_ORIGEN",
    "CN_EMPRESA_ASIGNADA",
)


def _safe_cell(value):
    value = clean_text(value)
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def recovery_csv(document):
    """Generate source cells from persisted rows and reject incomplete evidence."""
    rows = document.get("rows") or []
    if not document.get("file_hash") or not rows:
        raise SourceFileError("Esta importación no conserva filas contables para generar un CSV de recuperación.")

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    saved = {}
    for row in rows:
        try:
            number = int(row.get("source_row") or 0)
        except (TypeError, ValueError) as exc:
            raise SourceFileError("Falta el número de una fila contable original.") from exc
        if number <= 0 or number in saved:
            raise SourceFileError("Las filas originales faltan o están repetidas; no se puede generar un CSV confiable.")
        if not row.get("source_account") or not (row.get("source_debit") or row.get("source_credit")):
            raise SourceFileError(f"Fila {number}: falta la cuenta o el importe contable original.")
        saved[number] = row
        assigned = document.get("employer") if document.get("bulk_source_hash") else ""
        if assigned == UNIDENTIFIED_EMPLOYER:
            assigned = ""
        values = (
            row.get("event_date"), row.get("source_account"),
            "M.N." if document.get("currency") == "NIO" else "M.E.",
            row.get("voucher") or row.get("accounting_entry"),
            row.get("accounting_reference"),
            row.get("source_description") or row.get("description"),
            row.get("source_debit") or 0, row.get("source_credit") or 0,
            row.get("tmov"), row.get("tdoc"), row.get("source_classification"),
            row.get("client_name"), row.get("loan_number"), row.get("employer_text"),
            number, assigned,
        )
        writer.writerow([_safe_cell(value) for value in values])

    content = stream.getvalue().encode("utf-8-sig")
    parsed = parse_accounting_movements("recovery.csv", content)
    parsed = apply_accounting_currency_override(
        parsed, document.get("currency"), document.get("manual_fx_rate")
    )
    if len(parsed) != len(saved) or any(
        record["source_row"] not in saved
        or record["accounting_source_key"] != saved[record["source_row"]].get("accounting_source_key")
        or record["event_type"] != saved[record["source_row"]].get("event_type")
        or money(record.get("amount_usd")) != money(saved[record["source_row"]].get("amount_usd"))
        or money(record.get("amount_nio")) != money(saved[record["source_row"]].get("amount_nio"))
        for record in parsed
    ):
        raise SourceFileError("El CSV reconstruido no coincide con la evidencia contable guardada; no se descargó.")
    return content


@frappe.whitelist()
def download_recovery_csv(import_name):
    document = frappe.get_doc("CN Accounting Import", import_name)
    document.check_permission("read")
    try:
        content = recovery_csv(document)
    except SourceFileError as exc:
        frappe.throw(str(exc), title=_("No se pudo generar el CSV"))
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", document.name)
    frappe.local.response.update(
        type="download", filename=f"{safe_name}-recuperacion.csv", filecontent=content,
    )
