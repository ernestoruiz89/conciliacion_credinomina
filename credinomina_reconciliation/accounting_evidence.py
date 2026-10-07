"""Exact original ledger evidence; a shared key alone is never a relationship."""
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.accounting_types import accounting_code
from credinomina_reconciliation.rounding import money


def evidence_signature(row):
    """Exclude client enrichment, converted amounts and bank/deposit amounts."""
    return (
        str(row.get("event_date") or row.get("source_date") or "")[:10],
        clean_text(row.get("source_account")),
        clean_text(row.get("source_currency")),
        clean_text(row.get("source_voucher") or row.get("voucher")),
        money(row.get("source_debit")), money(row.get("source_credit")),
        clean_text(row.get("source_description") or row.get("description")).casefold(),
        clean_text(row.get("accounting_reference")),
        accounting_code(row.get("tmov")), accounting_code(row.get("tdoc")),
    )


def same_ledger_evidence(first, second):
    """Match money/date/account/voucher/description and available provenance.

    Equal lines at different physical positions remain independent. Missing
    evidence is not proof of a mirror, even when a stale key happens to match.
    """
    signature = evidence_signature(first)
    if not all(signature[:3]) or not any(signature[4:6]):
        return False
    if signature != evidence_signature(second):
        return False
    for field in ("source_file_hash", "source_row"):
        left, right = first.get(field), second.get(field)
        if left and right and str(left) != str(right):
            return False
    return True


def is_deposit(row):
    if row.get("event_type") and row["event_type"] != "Deposito":
        return False
    classification = row.get("accounting_classification")
    return classification == "Depósito" if classification else row.get("event_type") == "Deposito"


CORE_REMOVAL_ROLE = "Eliminar Mov. del Core"


def has_core_removal_role():
    """Require an explicit assignment, including for Administrator."""
    import frappe

    return bool(
        frappe.db.exists("Role", {"name": CORE_REMOVAL_ROLE, "disabled": 0})
        and frappe.db.exists("Has Role", {
            "parent": frappe.session.user, "parenttype": "User",
            "parentfield": "roles", "role": CORE_REMOVAL_ROLE,
        })
    )


def _is_core_import(document):
    import frappe

    if document.get("doctype") != "CN Accounting Import":
        return document.get("accounting_source_key") or (
            document.get("name") and frappe.db.get_value(
                document.doctype, document.name, "accounting_source_key"
            )
        )

    markers = ("file_hash", "bulk_source_hash", "imported_on")
    if any(document.get(field) for field in markers):
        return True
    if document.get("rows") and (document.get("source_file") or any(
        row.get("accounting_source_key") for row in document.rows
    )):
        return True
    if not document.get("name"):
        return False
    # Never trust a cancellation payload that clears the saved provenance.
    saved = frappe.db.get_value(document.doctype, document.name,
                                [*markers, "source_file"], as_dict=True)
    if saved and any(saved.get(field) for field in markers):
        return True
    filters = {"parent": document.name, "parenttype": document.doctype, "parentfield": "rows"}
    if not (saved and saved.get("source_file")):
        filters["accounting_source_key"] = ["!=", ""]
    return bool(frappe.db.exists("CN Source Row", filters))


def guard_imported_removal(document, action):
    import frappe
    from frappe import _

    if not _is_core_import(document) or has_core_removal_role():
        return
    message = _("No se puede {0} un registro importado del histórico contable sin tener asignado el rol {1}.").format(action, CORE_REMOVAL_ROLE)
    if document.get("doctype") == "CN Remittance Allocation":
        message += " " + _("Use Desconciliar si necesita retirar la distribución del depósito y corregir sus destinos.")
    frappe.throw(message)
