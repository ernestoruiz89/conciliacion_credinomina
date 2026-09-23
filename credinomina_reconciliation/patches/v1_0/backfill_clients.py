"""Link existing collection rows to the new client catalog without guessing."""

import frappe

from credinomina_reconciliation.client_identity import choose_client
from credinomina_reconciliation.client_registry import ClientIndex
from credinomina_reconciliation.parsers import (
    _extract_application_client_name,
    _extract_receipt,
    clean_text,
)


def execute():
    clients = ClientIndex()
    rows = frappe.get_all(
        "CN Collection Row",
        fields=[
            "name", "source_row", "client", "client_name",
            "client_number", "national_id",
        ],
        limit_page_length=100000,
    )
    for row in rows:
        if row.client or not clean_text(row.client_name):
            continue
        _existing, reason = choose_client(row, clients.records)
        if reason.startswith("Conflicto") or reason.startswith("Nombre ambiguo"):
            continue  # Leave conflicting historical data for manual review.
        client_name = clients.ensure_from_collection(row)
        frappe.db.set_value(
            "CN Collection Row", row.name, "client", client_name,
            update_modified=False,
        )

    for row in frappe.get_all(
        "CN Source Row",
        filters={"event_type": "Aplicacion"},
        fields=[
            "name", "description", "voucher", "client_name",
            "accounting_entry", "receipt",
        ],
        limit_page_length=100000,
    ):
        if "NOTA AL PRESTAMO" not in clean_text(row.description).upper():
            continue
        updates = {}
        if not row.client_name:
            updates["client_name"] = _extract_application_client_name(row.description)
        if not row.accounting_entry:
            updates["accounting_entry"] = row.voucher
        if not row.receipt:
            updates["receipt"] = _extract_receipt(row.description)
        updates = {key: value for key, value in updates.items() if value}
        if updates:
            frappe.db.set_value("CN Source Row", row.name, updates, update_modified=False)
