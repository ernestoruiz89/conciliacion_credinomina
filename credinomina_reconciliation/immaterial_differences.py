"""Document one-cent USD differences without moving money or replacing manual notes."""
from credinomina_reconciliation.application_quality import collection_quality, application_cash_covered
from credinomina_reconciliation.rounding import CENT_PRECISION, money

REASON = "Diferencia Inmaterial"
NOTES = "Por conversion de moneda"


def automatic_documentation(row, differences):
    if any(str(row.get(field) or "").strip() for field in ("difference_reason", "difference_notes")):
        return {}
    if not differences or any(value is None for value in differences):
        return {}
    nonzero = [abs(money(value)) for value in differences if money(value)]
    # A cent in one comparison cannot conceal a larger gap in another.
    if not nonzero or any(value != CENT_PRECISION for value in nonzero):
        return {}
    return {"difference_reason": REASON, "difference_notes": NOTES}


def candidates(document):
    doctype = document.get("doctype")
    if document.get("docstatus") == 2:
        return
    if doctype == "CN Remittance Allocation" and document.get("docstatus") == 1:
        yield document, (document.get("unclassified_usd"),)
        for row in document.get("detail_rows") or []:
            yield row, (row.get("pending_usd"),)
    elif doctype == "CN Accounting Import" and document.get("status") in {"Importado", "Importado con excepciones"}:
        for row in document.get("rows") or []:
            if (row.get("event_type") == "Aplicacion" and row.get("effective")
                    and row.get("match_status") != "Ignorado" and row.get("historical_period")):
                yield row, (row.get("historical_balance_usd"),)
    elif doctype == "CN Reconciliation Period" and not document.get("no_collection") and document.get("reconciliation_mode") != "Historica":
        for row in document.get("collection_rows") or []:
            quality = collection_quality(row)["quality_difference_usd"]
            if quality is not None and money(quality):
                # Collection/application quality is independent of cash coverage.
                yield row, (quality,)
                continue
            try:
                coverage = money(row.get("applied_usd")) - money(application_cash_covered(row))
            except (ValueError, TypeError, AttributeError):
                coverage = None
            yield row, (quality, coverage)


def classify_document(document, *, row_names=None):
    """Internal, after reconciliation; update only the two documentation fields."""
    import frappe
    from credinomina_reconciliation.difference_documentation import _store_documentation, _touch_documentation
    from credinomina_reconciliation.difference_reasons import seed_reasons
    pending = [(row, differences) for row, differences in candidates(document)
               if row_names is None or row.get("name") in row_names
               if automatic_documentation(row, differences)]
    if not pending:
        return 0
    # Manual documentation uses this same parent lock. Re-read its two fields
    # so a classification saved since reconciliation began is never overwritten.
    frappe.db.get_value(document.doctype, document.name, "name", for_update=True)
    changes = []
    for row, differences in pending:
        saved = frappe.db.get_value(row.doctype, row.name,
            ["difference_reason", "difference_notes"], as_dict=True, for_update=True)
        if saved is None:
            continue
        row.update(saved)
        if values := automatic_documentation(row, differences):
            changes.append((row, values))
    if not changes:
        return 0
    if not frappe.db.exists("CN Difference Reason", REASON):
        seed_reasons()
    for row, values in changes:
        _store_documentation(document, row, values)
        row.update(values)
    document.modified = _touch_documentation(document)
    return len(changes)
