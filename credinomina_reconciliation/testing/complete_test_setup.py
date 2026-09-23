"""Finish Frappe's first-run wizard on the isolated reconciliation test site."""

import frappe

from frappe.desk.page.setup_wizard.setup_wizard import setup_complete


TEST_SITE = "cn-reconciliation-test.local"


def run():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    if not frappe.is_setup_complete():
        result = setup_complete({
            "language": "Español (Nicaragua)",
            "lang": "Español (Nicaragua)",
            "country": "Nicaragua",
            "timezone": "America/Managua",
            "currency": "NIO",
            "enable_telemetry": 0,
        })
        if result.get("status") != "ok":
            raise AssertionError(result)
    settings = frappe.get_single("System Settings")
    if not frappe.is_setup_complete() or settings.language != "es-NI":
        raise AssertionError({
            "setup_complete": frappe.is_setup_complete(),
            "language": settings.language,
        })
    frappe.db.commit()
    return {
        "setup_complete": True,
        "language": settings.language,
        "country": settings.country,
        "currency": settings.currency,
        "timezone": settings.time_zone,
    }
