"""Deterministic, read-only grouping of accounting applications."""

from collections import defaultdict
from datetime import date, datetime
import csv
import io

from credinomina_reconciliation.employer_naming import employer_alias_index, employer_label_key
from credinomina_reconciliation.parsers import SourceFileError, clean_text, source_key
from credinomina_reconciliation.reconciliation import duplicate_business_key
from credinomina_reconciliation.rounding import sum_money


def movement_key(record):
    # Same business identity used by reconciliation (includes voucher and date).
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
        writer.writerow([source_row if column == "cn_fila_origen" else cell(original.get(column))
                         for column in columns])
    return stream.getvalue().encode("utf-8-sig")


def group_applications(records, employers, fallback_employer="", existing=()):
    aliases, ambiguous = employer_alias_index(employers)
    names = {row["name"] for row in employers}
    existing = set(existing)
    groups = defaultdict(list)
    issues, duplicates, excluded = [], [], []
    seen = set()
    for original in records:
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
        label = employer_label_key(row.get("employer_text"))
        from_file = aliases.get(label, "")
        from_portfolio = clean_text(row.get("portfolio_employer"))
        if label in ambiguous:
            reasons.append("Empresa o alias ambiguo")
        elif label and not from_file:
            reasons.append("Empresa del movimiento no registrada; agregue su alias al convenio")
        if from_file and from_portfolio and from_file != from_portfolio:
            reasons.append("La empresa del movimiento no coincide con la cartera")
        employer = from_file or from_portfolio or fallback_employer
        if employer not in names:
            reasons.append("No se pudo identificar una empresa de convenio")
        if fallback_employer and employer != fallback_employer:
            reasons.append("La fila pertenece a otra empresa; quite la empresa predeterminada para un archivo mixto")
        if row.get("portfolio_validation_status") in {
            "Crédito duplicado en corte", "Número de cliente ambiguo en el corte",
            "Número de cliente del movimiento no coincide con el crédito en cartera",
        }:
            reasons.append(row["portfolio_validation_status"])
        if reasons:
            issues.append({"row": row.get("source_row"), "loan_number": row.get("loan_number"),
                           "reason": "; ".join(reasons)})
            continue
        key = (employer, movement_key(row))
        if key in existing or key in seen:
            duplicates.append({"row": row.get("source_row"), "reason": "Aplicación ya importada o repetida en el archivo"})
            continue
        seen.add(key)
        row["event_date"] = event_date
        # Preserve the original employer text; use this resolved value only for grouping.
        groups[employer, event_date].append(row)
    result = [{"employer": employer, "event_date": event_date, "rows": rows,
               "count": len(rows), "total_usd": sum_money(row.get("amount_usd") for row in rows)}
              for (employer, event_date), rows in sorted(groups.items())]
    return {"groups": result, "issues": issues, "duplicates": duplicates, "excluded": excluded}
