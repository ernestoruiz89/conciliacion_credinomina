"""Complete collection identities from the latest available cut through its month."""
from collections import defaultdict

import frappe
from frappe.utils import get_last_day

from credinomina_reconciliation.credit_lookup import get_credit_rows
from credinomina_reconciliation.parsers import (
    SourceFileError, canonical_credit_number, canonical_identifier, clean_text,
)


CLIENT_FIELDS = {"client_name": "client_name", "client_number": "client_number_core",
                 "national_id": "national_id"}


def enrich_collection_records(records, employer, cutoff_date):
    """Fill blanks only; do not substitute a different month, employer or credit."""
    cuts = []
    if cutoff_date and frappe.has_permission("CN Credit Portfolio Snapshot", "read"):
        cuts = frappe.get_list("CN Credit Portfolio Snapshot", filters={
            "disabled": 0, "docstatus": ["!=", 2],
            "status": ["in", ["Importado", "Importado con alertas"]],
            "report_date": ["<=", get_last_day(cutoff_date)],
        }, fields=["name", "report_date"], order_by="report_date desc, name desc", limit_page_length=1)
    cut = cuts[0] if cuts else None
    by_credit = defaultdict(list)
    if cut:
        rows = get_credit_rows("CN Credit Portfolio Row", filters={
            "parent": cut.name, "parenttype": "CN Credit Portfolio Snapshot", "parentfield": "rows",
        }, fields=["credit_number", "client_name", "client_number_core", "national_id", "employer"],
            loan_field="credit_number", loans=[record.get("loan_number") for record in records])
        for row in rows:
            by_credit[canonical_credit_number(row.credit_number)].append(row)

    completed = 0
    for record in records:
        loan = clean_text(record.get("loan_number"))
        prefix = f"Fila {record['source_row']}, crédito {loan or 'sin informar'}: "
        if not loan:
            raise SourceFileError(prefix + "indique Nro. Crédito en la cobranza.")
        matches = by_credit.get(canonical_credit_number(loan), [])
        if not matches:
            if not clean_text(record.get("client_name")):
                reason = (f"el crédito no aparece en la cartera {cut.name}." if cut else
                          "no hay una cartera importada, activa y accesible del mes del corte o anterior.")
                raise SourceFileError(prefix + "no se pudo completar el nombre del cliente; " + reason)
            continue
        if len(matches) != 1:
            raise SourceFileError(prefix + f"el crédito aparece varias veces en la cartera {cut.name}; revise la duplicidad.")
        match = matches[0]
        if match.employer != employer:
            raise SourceFileError(prefix + f"la empresa del crédito en la cartera {cut.name} no coincide con la del período.")
        for field in ("client_number", "national_id"):
            provided = canonical_identifier(record.get(field))
            portfolio = canonical_identifier(match.get(CLIENT_FIELDS[field]))
            if provided and portfolio and provided != portfolio:
                label = "el número de cliente" if field == "client_number" else "la cédula"
                raise SourceFileError(prefix + f"{label} no coincide con la cartera {cut.name}.")
        changed = False
        for field, portfolio_field in CLIENT_FIELDS.items():
            value = clean_text(match.get(portfolio_field))
            if not clean_text(record.get(field)) and value:
                record[field] = value
                changed = True
        if not clean_text(record.get("client_name")):
            raise SourceFileError(prefix + f"la cartera {cut.name} tampoco tiene nombre del cliente.")
        completed += bool(changed)
    return {"snapshot": cut.name if cut else None,
            "report_date": str(cut.report_date) if cut else None, "completed_rows": completed}
