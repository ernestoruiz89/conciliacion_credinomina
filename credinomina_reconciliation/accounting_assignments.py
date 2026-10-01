"""Explicit, file-scoped company decisions made in the bulk preview."""
from credinomina_reconciliation.employer_naming import AccountingEmployerResolver, UNIDENTIFIED_EMPLOYER
from credinomina_reconciliation.parsers import SourceFileError


def apply_assignments(records, employers, assignments):
    if not isinstance(assignments, dict):
        raise SourceFileError("Las asignaciones de empresa deben indicar fila y empresa.")
    resolver = AccountingEmployerResolver(employers)
    by_row = {str(row["source_row"]): row for row in records}
    for number, company in assignments.items():
        row = by_row.get(str(number))
        if row is None:
            raise SourceFileError(f"La fila {number} ya no está en el archivo. Genere un nuevo análisis.")
        if not isinstance(company, str) or company not in resolver.names or company == UNIDENTIFIED_EMPLOYER:
            raise SourceFileError(f"Fila {number}: seleccione una empresa registrada y accesible.")
        resolved, issue = resolver.resolve_for_import(row)
        if issue or resolved not in {UNIDENTIFIED_EMPLOYER, company}:
            raise SourceFileError(f"Fila {number}: la selección contradice la identificación actual ({issue or resolved}). Revise la cartera o el alias.")
        row["_manual_employer"] = company
    return records
