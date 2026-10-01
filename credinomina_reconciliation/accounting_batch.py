"""Deterministic, read-only grouping of accounting applications."""

from collections import defaultdict
from datetime import date, datetime
import csv
import io

from credinomina_reconciliation.employer_naming import AccountingEmployerResolver, UNIDENTIFIED_EMPLOYER
from credinomina_reconciliation.parsers import SourceFileError, clean_text, source_key
from credinomina_reconciliation.reconciliation import duplicate_business_key
from credinomina_reconciliation.rounding import sum_money


def movement_key(record):
    # Similarity for review only, never proof that two physical lines are one.
    return source_key(*duplicate_business_key(record))


def accounting_group_csv(source_records, rows):
    """Export source cells, not converted USD values, for lossless reprocessing."""
    selected = []
    columns = {}
    for row in rows:
        original = source_records.get(row["source_row"])
        if original is None:
            raise SourceFileError("No se encontró la fila original para generar el CSV individual.")
        selected.append((row["source_row"], original))
        columns.update(dict.fromkeys(original))
    columns["cn_fila_origen"] = None
    manual = {row["source_row"]: row.get("_manual_employer") or "" for row in rows}
    if any(manual.values()):
        columns["cn_empresa_asignada"] = None
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([column.upper() for column in columns])

    def cell(value):
        if isinstance(value, (datetime, date)):
            return value.strftime("%Y-%m-%d")
        text = clean_text(value)
        # Spreadsheet-safe text; clean_text removes the quote when importing.
        if text.startswith(("=", "+", "-", "@")):
            text = "'" + text
        return text

    for source_row, original in selected:
        writer.writerow([source_row if column == "cn_fila_origen" else cell(manual[source_row]) if column == "cn_empresa_asignada" else cell(original.get(column))
                         for column in columns])
    return stream.getvalue().encode("utf-8-sig")


def group_applications(records, employers, fallback_employer="", existing=()):
    resolver = AccountingEmployerResolver(employers)
    existing = set(existing)
    groups = defaultdict(list)
    issues, duplicates, excluded = [], [], []
    seen = set()
    for position, original in enumerate(records):
        row = dict(original)
        if row.get("event_type") != "Aplicacion":
            excluded.append({"row": row.get("source_row"), "reason": "No es una aplicación de pago"})
            continue
        reasons = []
        raw_date = str(row.get("event_date") or "")[:10]
        try:
            event_date = date.fromisoformat(raw_date).isoformat()
        except ValueError:
            event_date = ""
            reasons.append("Falta una fecha de aplicación válida")
        employer, employer_issue = resolver.resolve_for_import(row, fallback_employer)
        if employer_issue:
            reasons.append(employer_issue)
        if fallback_employer and employer and employer not in {fallback_employer, UNIDENTIFIED_EMPLOYER} and not row.get("_manual_employer"):
            reasons.append("La fila pertenece a otra empresa; quite la empresa predeterminada para un archivo mixto")
        if reasons:
            issues.append({"row": row.get("source_row"), "loan_number": row.get("loan_number"),
                           "reason": "; ".join(reasons)})
            continue
        key = (employer, movement_key(row))
        if key in existing or key in seen:
            duplicates.append({"row": row.get("source_row"), "reason": "Aplicación similar a otra línea del archivo o de una carga previa; se importará para revisión"})
        seen.add(key)
        row["event_date"] = event_date
        # Preserve the original employer text; use this resolved value only for grouping.
        # Unknown companies may represent unrelated employers even on one date.
        # Give each physical movement its own editable import document.
        groups[employer, event_date, position if employer == UNIDENTIFIED_EMPLOYER else -1].append(row)
    result = [{"employer": employer, "event_date": event_date, "rows": rows,
               "count": len(rows), "total_usd": sum_money(row.get("amount_usd") for row in rows)}
              for (employer, event_date, _position), rows in sorted(groups.items())]
    return {"groups": result, "issues": issues, "duplicates": duplicates, "excluded": excluded}
