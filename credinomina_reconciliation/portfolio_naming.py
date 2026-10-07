"""Stable snapshot names derive from the reporting month in the portfolio file."""

import frappe
from frappe import _
from frappe.utils import getdate


DOCTYPE = "CN Credit Portfolio Snapshot"


def portfolio_snapshot_name(report_date):
    if not report_date:
        frappe.throw(_("El corte de cartera requiere fecha de reporte para nombrarlo."))
    cut_date = getdate(report_date)
    return f"CARTERA-{cut_date.month}-{cut_date.year}"


def rename_snapshot_for_date(snapshot, report_date, previous_report_date=None):
    """Rename a saved snapshot and let Frappe update linked data and attachments."""
    automatic_name = portfolio_snapshot_name(previous_report_date) if previous_report_date else None
    if not snapshot.name.startswith("CARTERA-BORRADOR-") and snapshot.name != automatic_name:
        return snapshot.name  # Keep a user's custom name when reimporting.
    new_name = portfolio_snapshot_name(report_date)
    if snapshot.name == new_name:
        return new_name
    base_name = new_name
    suffix = 2
    while frappe.db.exists(DOCTYPE, new_name):
        new_name = f"{base_name}-{suffix}"
        suffix += 1
    old_name = snapshot.name
    renamed = frappe.rename_doc(
        DOCTYPE, old_name, new_name, force=True, merge=False,
        show_alert=False, rebuild_search=False,
    )
    snapshot.name = renamed
    snapshot.localname = old_name
    for child in snapshot.get_all_children():
        child.parent = renamed
    return renamed
