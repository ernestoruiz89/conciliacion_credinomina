"""Presentation only: never use formatted dates for matching or storage keys."""


def display_date(value):
    if not value:
        return ""
    import frappe
    from frappe.utils import formatdate

    # A business date must not shift when a timestamp is viewed in another zone.
    date_format = frappe.db.get_single_value("System Settings", "date_format") or "yyyy-mm-dd"
    return formatdate(str(value)[:10], date_format)
