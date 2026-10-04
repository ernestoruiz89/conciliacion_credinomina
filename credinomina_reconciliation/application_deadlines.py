"""Capture the payment term once, without pretending it was the historical contract."""
from datetime import date

import frappe

from credinomina_reconciliation.application_aging import deposit_due_date
from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER

FIELDS = ("payment_due_date", "payment_term_days", "payment_term_origin")
RECORDED = "Plazo vigente al registrar"
MIGRATED = "Plazo vigente al migrar; no acredita el plazo histórico"
CORRECTED = "Plazo vigente al corregir empresa o fecha"


def deadline_values(row, employer, grace_days, origin=RECORDED):
    if row.get("event_type") != "Aplicacion" or not row.get("event_date") or not employer or employer == UNIDENTIFIED_EMPLOYER:
        return dict(zip(FIELDS, (None, 0, "Empresa o fecha pendiente de identificar")))
    days = max(int(grace_days or 10), 1)
    return dict(zip(FIELDS, (deposit_due_date(row.get("event_date"), days), days, origin)))


def _date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def freeze_deadlines(document):
    previous = document.get_doc_before_save()
    old_rows = {row.name: row for row in (previous.get("rows") or [])} if previous else {}
    employer = document.get("employer")
    days = None
    for row in document.get("rows") or []:
        old = old_rows.get(row.name)
        unchanged = old and previous.get("employer") == employer and old.get("event_type") == row.get("event_type") and _date(old.get("event_date")) == _date(row.get("event_date"))
        if unchanged and old.get("payment_due_date"):
            row.update({field: old.get(field) for field in FIELDS})
            continue  # Ignore client-supplied values and subsequent grace_days changes.
        if days is None:
            days = frappe.db.get_value("CN Employer", employer, "grace_days") or 10
        origin = MIGRATED if unchanged else CORRECTED if old else RECORDED
        row.update(deadline_values(row, employer, days, origin))
