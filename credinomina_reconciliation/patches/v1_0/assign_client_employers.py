"""Backfill company ownership for clients already linked to collection rows."""

from collections import defaultdict

import frappe


def execute():
    if not frappe.db.exists("DocType", "CN Client"):
        return
    employers = {
        row.name: row.employer for row in frappe.get_all(
            "CN Reconciliation Period", fields=["name", "employer"],
            limit_page_length=100000,
        )
    }
    by_client = defaultdict(set)
    for row in frappe.get_all(
        "CN Collection Row", fields=["client", "parent"],
        filters={"client": ["is", "set"]}, limit_page_length=100000,
    ):
        employer = employers.get(row.parent)
        if employer:
            by_client[row.client].add(employer)
    for client, owned_by in by_client.items():
        if len(owned_by) != 1:
            continue  # Conflicting history requires a human decision.
        current = frappe.db.get_value("CN Client", client, "employer")
        employer = next(iter(owned_by))
        if not current:
            frappe.db.set_value(
                "CN Client", client, "employer", employer, update_modified=False,
            )
