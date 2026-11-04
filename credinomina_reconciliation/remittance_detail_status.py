"""Filterable review labels, independent of financial reconciliation results."""

RESOLVED = {"Conciliada", "No deducido", "Conciliada con diferencia documentada", "Diferencia documentada"}


def review_status(value):
    status = str(value or "").strip()
    if status in RESOLVED or status == "Revisar" or status.startswith(("Revisar:", "Revisar ")):
        return status
    return "Revisar: " + (status or "Pendiente")
