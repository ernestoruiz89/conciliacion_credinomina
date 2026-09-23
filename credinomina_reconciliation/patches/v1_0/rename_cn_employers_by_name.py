"""Make employer_name the document name while preserving employer_code and links."""

import frappe
from frappe import _
from frappe.model.naming import validate_name

from credinomina_reconciliation.employer_naming import build_employer_rename_plan


def execute():
    if not frappe.db.table_exists("CN Employer"):
        return
    if frappe.get_meta("CN Employer").autoname != "field:employer_name":
        frappe.throw(_("Sincronice el DocType CN Employer antes de ejecutar este parche."))
    employers = frappe.get_all(
        "CN Employer",
        fields=["name", "employer_name", "employer_code"],
        order_by="name asc",
        limit_page_length=100000,
    )
    if not employers:
        return
    try:
        normalized, renames = build_employer_rename_plan(employers)
    except ValueError as exc:
        frappe.throw(_("No se pueden renombrar las empresas: {0}").format(str(exc)))

    # Validate every final target before the first write. Do not merge companies.
    for target in normalized.values():
        validate_name("CN Employer", target)

    for employer in employers:
        target = normalized[employer.name]
        if employer.employer_name != target:
            frappe.db.set_value(
                "CN Employer", employer.name, "employer_name", target,
                update_modified=False,
            )

    # Release all code-based names before assigning company names. This also
    # handles cases where one company's name equals another's code.
    for old, temporary, _target in renames:
        frappe.rename_doc(
            "CN Employer", old, temporary, force=True,
            ignore_permissions=True, show_alert=False, rebuild_search=False,
        )
    for _old, temporary, target in renames:
        frappe.rename_doc(
            "CN Employer", temporary, target, force=True,
            ignore_permissions=True, show_alert=False, rebuild_search=False,
        )
    for employer in employers:
        target = normalized[employer.name]
        migrated = frappe.db.get_value(
            "CN Employer", target, ["employer_name", "employer_code"], as_dict=True
        )
        if (
            not migrated or migrated.employer_name != target
            or migrated.employer_code != employer.employer_code
        ):
            frappe.throw(_(
                "No se conservó el nombre o código de la empresa {0}."
            ).format(employer.name))
