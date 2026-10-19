"""Keep imported source files available for reprocessing and audit."""

import frappe
from frappe import _

from credinomina_reconciliation.accounting_evidence import (
    CORE_REMOVAL_ROLE,
    has_core_removal_role,
)


# (document, file URL field, provenance field). A file selected for a draft
# becomes protected only after its import has recorded provenance.
IMPORTED_FILES = (
    ("CN Accounting Import", "source_file", "file_hash"),
    ("CN Accounting Import", "bulk_source_file", "bulk_source_hash"),
    ("CN Credit Portfolio Snapshot", "source_file", "file_hash"),
    ("CN Reconciliation Period", "collection_file", "collection_import_sha256"),
    ("CN Reconciliation Period", "employer_response_file", "employer_response_import_key"),
    ("CN Remittance Allocation", "source_file", "source_file_hash"),
    ("CN Remittance Allocation", "detail_source_file", "detail_hash"),
    ("CN Complementary Item", "source_file", "source_file_hash"),
    ("CN Deposit Accounting Source", "source_file", "source_file_hash"),
)


def guard_import_file_deletion(document, method=None):
    """Guard every File reference to a blob still used by an imported source.

    Frappe can create multiple File records with the same URL. Checking only
    the attachment's parent would let deletion of a shared reference remove
    the physical file that an import still needs.
    """
    url = document.get("file_url")
    if not url:
        return
    for doctype, file_field, provenance_field in IMPORTED_FILES:
        if frappe.db.exists(doctype, {
            file_field: url,
            provenance_field: ["!=", ""],
        }):
            if not has_core_removal_role():
                frappe.throw(
                    _("No se puede eliminar un archivo utilizado en una importación sin tener asignado el rol {0}.").format(
                        CORE_REMOVAL_ROLE
                    ),
                    frappe.PermissionError,
                )
            return
