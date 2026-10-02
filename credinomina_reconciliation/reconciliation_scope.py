"""Bounded, read-only boundary audit before loading scoped reconciliation documents."""
import hashlib
import json

import frappe
from frappe import _

from credinomina_reconciliation.employer_naming import AccountingEmployerResolver, attach_employer_aliases


BOUNDARY_FIELDS = [
    "parent", "portfolio_employer", "portfolio_validation_status", "employer_text",
    "event_type", "historical_period", "collection_period", "collection_row_id",
    "application_allocation_detail", "allocation_detail",
]


def _entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _link_maps(rows):
    collections, items = set(), set()
    for row in rows:
        if row.event_type == "Deposito":
            continue
        if row.collection_row_id:
            collections.add(row.collection_row_id)
        for entry in _entries(row.application_allocation_detail):
            if isinstance(entry, dict) and entry.get("collection_row_id"):
                collections.add(entry["collection_row_id"])
        for entry in _entries(row.allocation_detail):
            if isinstance(entry, dict) and entry.get("partida"):
                items.add(entry["partida"])
    result = []
    for doctype, names, field in (
        ("CN Collection Row", collections, "parent"),
        ("CN Complementary Item", items, "period"),
    ):
        result.append({row.name: row.get(field) for row in frappe.get_all(
            doctype, filters={"name": ["in", sorted(names)]}, fields=["name", field],
            limit_page_length=0,
        )} if names else {})
    return result


def load_scoped_imports(companies):
    """Keep cross-company guards, without materializing unrelated documents.

    Audit only boundary evidence, in bounded pages. Even an incorrectly assigned
    import outside the scope must block if it already affects this cash pool.
    No dates are excluded: a deposit can settle applications from earlier months.
    """
    companies = set(companies)
    imports = frappe.get_all(
        "CN Accounting Import", filters={"status": ["in", ["Importado", "Importado con excepciones"]]},
        fields=["name", "employer", "historical_period"], order_by="creation asc", limit_page_length=0,
    )
    period_employers = {row.name: row.employer for row in frappe.get_all(
        "CN Reconciliation Period", fields=["name", "employer"], limit_page_length=0,
    )}
    employers = frappe.get_all(
        "CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0,
    )
    attach_employer_aliases(employers)
    resolver = AccountingEmployerResolver(employers)

    def check(document, related):
        related = set(related) - {None, ""}
        conflicts = (related - {document.employer} if document.employer in companies
                     else related & companies)
        if conflicts:
            frappe.throw(_(
                "La importación {0} tiene datos o vínculos de {1} que no coinciden con su empresa. "
                "Revise la empresa y los períodos de esa importación antes de conciliar."
            ).format(document.name, ", ".join(sorted(conflicts))))

    for document in imports:
        check(document, [period_employers.get(document.historical_period)])
    for start in range(0, len(imports), 200):
        parents = {doc.name: doc for doc in imports[start:start + 200]}
        offset = 0
        while True:
            rows = frappe.get_all(
                "CN Source Row", filters={"parent": ["in", list(parents)], "parenttype": "CN Accounting Import"},
                fields=BOUNDARY_FIELDS, order_by="name asc", limit_start=offset, limit_page_length=1000,
            )
            if not rows:
                break
            collections, items = _link_maps(rows)
            for row in rows:
                related = {row.portfolio_employer or resolver.resolve(row)[0]}
                # Shared-payer destinations do not change ownership of a deposit.
                if row.event_type != "Deposito":
                    periods = {row.historical_period, row.collection_period, collections.get(row.collection_row_id)}
                    for field in ("application_allocation_detail", "allocation_detail"):
                        for entry in _entries(row.get(field)):
                            if isinstance(entry, dict):
                                periods.update((entry.get("period"), entry.get("periodo"),
                                                collections.get(entry.get("collection_row_id")),
                                                items.get(entry.get("partida"))))
                    related.update(period_employers.get(period) for period in periods)
                check(parents[row.parent], related)
            offset += len(rows)
            if len(rows) < 1000:
                break
    documents = []
    for item in imports:
        if item.employer in companies:
            document = frappe.get_doc("CN Accounting Import", item.name)
            document.check_permission("write")
            documents.append(document)
    return documents


def document_state(document):
    """Compact fingerprint of stored fields, excluding transient engine attributes."""
    def stored(value):
        if isinstance(value, dict):
            return {key: stored(item) for key, item in value.items() if not key.startswith("_")}
        if isinstance(value, (list, tuple)):
            return [stored(item) for item in value]
        return value

    return hashlib.sha256(json.dumps(
        stored(document.as_dict()), sort_keys=True, ensure_ascii=False, default=str,
    ).encode("utf-8")).digest()
