"""Bounded closure checks, retaining cross-period and authorized-payer evidence."""
import json

import frappe

from credinomina_reconciliation.remittance_periods import deposit_names_for_periods
from credinomina_reconciliation.rounding import CASH_EPSILON


def entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


class ClosureScope:
    """One request's cached, freshly reconciled company pool and related deposits."""
    def __init__(self, period, companies=None):
        self.period = period
        self._companies = sorted(set(companies)) if companies is not None else None
        self._application_ids = None
        self._linked_application_ids = None
        self._complementary_ids = None
        self._source_parents = None
        self._deposits = None

    @property
    def companies(self):
        if self._companies is None:
            from credinomina_reconciliation.paying_employers import reconciliation_companies
            self._companies = reconciliation_companies(self.period.employer)
        return self._companies

    def application_ids(self):
        if self._application_ids is None:
            self._application_ids = frappe.get_all("CN Source Row", filters={
                "historical_period": self.period.name, "event_type": "Aplicacion", "effective": 1,
            }, pluck="name", limit_page_length=0)
        return self._application_ids

    def complementary_ids(self):
        if self._complementary_ids is None:
            self._complementary_ids = frappe.get_all("CN Complementary Item", filters={
                "period": self.period.name,
            }, pluck="name", limit_page_length=0)
        return self._complementary_ids

    def linked_application_ids(self):
        # Invalid targets still need review: include ineffective applications for
        # evidence discovery, while closure totals use only effective applications.
        if self._linked_application_ids is None:
            self._linked_application_ids = frappe.get_all("CN Source Row", filters={
                "historical_period": self.period.name, "event_type": "Aplicacion",
            }, pluck="name", limit_page_length=0)
        return self._linked_application_ids

    def source_parents(self):
        if self._source_parents is None:
            self._source_parents = frappe.get_all("CN Accounting Import", filters={
                "employer": ["in", self.companies],
            }, pluck="name", limit_page_length=0)
        return self._source_parents

    def related_deposits(self):
        if self._deposits is not None:
            return self._deposits
        related = set(deposit_names_for_periods([self.period.name]))
        related.update(frappe.get_all("CN Remittance Target", filters={
            "period": self.period.name, "parenttype": "CN Remittance Allocation",
        }, pluck="parent", limit_page_length=0))
        for field, identifiers in (("historical_application", self.linked_application_ids()),
                                   ("complementary_item", self.complementary_ids())):
            for offset in range(0, len(identifiers), 500):
                related.update(frappe.get_all("CN Remittance Target", filters={
                    field: ["in", identifiers[offset:offset + 500]], "parenttype": "CN Remittance Allocation",
                }, pluck="parent", limit_page_length=0))
        fields = ["name", "allocation_detail", "detail_status", "unclassified_usd"]
        deposits = frappe.get_all("CN Remittance Allocation", filters={
            "docstatus": 1, "employer": ["in", self.companies],
        }, fields=fields, limit_page_length=0)
        by_name = {deposit.name: deposit for deposit in deposits}
        # Explicit child links outside the current payer map still participate in
        # closure guards; do not discard evidence solely because its owner differs.
        extra = sorted(related - set(by_name))
        for offset in range(0, len(extra), 500):
            for deposit in frappe.get_all("CN Remittance Allocation", filters={
                "docstatus": 1, "name": ["in", extra[offset:offset + 500]],
            }, fields=fields, limit_page_length=0):
                by_name[deposit.name] = deposit
        complementary = set(self.complementary_ids())
        self._deposits = [deposit for name, deposit in by_name.items() if name in related or any(
            entry.get("periodo") == self.period.name or entry.get("partida") in complementary
            for entry in entries(deposit.allocation_detail)
        )]
        return self._deposits

    def has_operative_application(self):
        if frappe.db.exists("CN Source Row", {
            "event_type": "Aplicacion", "effective": 1, "collection_period": self.period.name,
        }):
            return True
        row_names = {row.name for row in self.period.collection_rows}
        if not row_names:
            return False
        parents = self.source_parents()
        for offset in range(0, len(parents), 500):
            for source in frappe.get_all("CN Source Row", filters={
                "event_type": "Aplicacion", "effective": 1,
                "parenttype": "CN Accounting Import", "parent": ["in", parents[offset:offset + 500]],
            }, fields=["application_allocation_detail"], limit_page_length=0):
                if any(entry.get("collection_row_id") in row_names for entry in entries(source.application_allocation_detail)):
                    return True
        return False

    def has_unclassified_source_deposit(self, references):
        parents = self.source_parents()
        for offset in range(0, len(parents), 500):
            if frappe.db.exists("CN Source Row", {
                "parent": ["in", parents[offset:offset + 500]], "event_type": "Deposito",
                "reference": ["in", sorted(references)], "unclassified_usd": [">", CASH_EPSILON],
            }):
                return True
        return False


def pending_registered_targets(target_filters):
    """Resolve only matching target parents, rather than every submitted deposit."""
    selections = [target_filters]
    for field, value in target_filters.items():
        if isinstance(value, list) and len(value) == 2 and value[0] == "in" and len(value[1]) > 500:
            selections = [{**target_filters, field: ["in", value[1][offset:offset + 500]]}
                          for offset in range(0, len(value[1]), 500)]
            break
    parents = sorted({parent for selection in selections for parent in frappe.get_all(
        "CN Remittance Target", filters={
            **selection, "result": ["!=", "Aplicada"], "parenttype": "CN Remittance Allocation",
        }, pluck="parent", limit_page_length=0,
    )})
    for offset in range(0, len(parents), 500):
        if frappe.db.exists("CN Remittance Allocation", {
            "name": ["in", parents[offset:offset + 500]], "docstatus": 1,
        }):
            return True
    return False
