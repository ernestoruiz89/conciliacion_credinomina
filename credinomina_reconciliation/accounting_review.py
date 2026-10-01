"""Non-payment accounting evidence. Drafts never participate in reconciliation."""

import frappe
from frappe import _

from credinomina_reconciliation.accounting_types import APPLICATION, DEBIT_NOTE
from credinomina_reconciliation.employer_naming import employer_alias_index, employer_label_key
from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import decimal_value

EVIDENCE_FIELDS = (
    "accounting_source_key", "tmov", "tdoc", "accounting_classification",
    "classification_reason", "source_classification", "source_account", "source_debit",
    "source_credit", "source_currency", "accounting_reference", "source_file", "source_row",
    "source_file_hash", "source_voucher", "source_description", "source_date", "source_fx_rate", "source_client_name",
)


def plan_review_items(records, employers, fallback=""):
    """Resolve only unambiguous companies; lack of identity does not discard evidence."""
    aliases, ambiguous = employer_alias_index(employers)
    names = {row["name"] for row in employers}
    items = []
    for record in records:
        if record.get("event_type") != "Ajuste":
            continue
        row = dict(record)
        label = employer_label_key(row.get("employer_text"))
        company = aliases.get(label, "") if label not in ambiguous else ""
        portfolio = clean_text(row.get("portfolio_employer"))
        conflict = bool(label and not company) or bool(company and portfolio and company != portfolio)
        row["resolved_employer"] = "" if conflict else (company or portfolio or fallback)
        if row["resolved_employer"] not in names:
            row["resolved_employer"] = ""
        items.append(row)
    return items


def create_review_items(records, source_file, file_hash):
    if not records:
        return []
    if not frappe.has_permission("CN Complementary Item", "create"):
        frappe.throw(_("Se requiere permiso para crear las partidas complementarias pendientes de revisión."), frappe.PermissionError)
    result = []
    for row in records:
        key = row["accounting_source_key"]
        existing = frappe.db.get_value("CN Complementary Item", {"accounting_source_key": key}, "name")
        if existing:
            frappe.get_doc("CN Complementary Item", existing).check_permission("read")
            row["complementary_item"] = existing
            continue
        if not row.get("event_date"):
            frappe.throw(_("La fila {0} no tiene fecha válida; corríjala antes de importar.").format(row["source_row"]))
        employer = row.get("resolved_employer") or ""
        if employer:
            frappe.get_doc("CN Employer", employer).check_permission("read")
        document = frappe.get_doc({
            "doctype": "CN Complementary Item", "category": "Por clasificar",
            "employer": employer, "posting_date": row["event_date"],
            "currency": "USD", "amount": row["amount_usd"],
            "loan_number": row.get("loan_number"), "client_number": row.get("client_number"),
            # NO_REF is accounting evidence, never an inferred deposit reference.
            "reference": "", "voucher": row.get("voucher"), "voucher_line": key,
            "description": row.get("description") or row["classification_reason"],
            **{field: row.get(field) for field in EVIDENCE_FIELDS if field in row},
            "source_file": source_file, "source_file_hash": file_hash,
            "source_voucher": row.get("voucher"), "source_description": row.get("source_description") or row.get("description"),
            "source_client_name": row.get("client_name") or row.get("portfolio_client_name") or "",
            "source_date": row["event_date"], "source_fx_rate": row.get("manual_fx_rate") or row.get("fx_rate") or 0,
            "review_action": "Pendiente de revisión",
        }).insert()
        original_file = frappe.get_doc("File", {"file_url": source_file})
        original_file.check_permission("read")
        frappe.get_doc({"doctype": "File", "file_name": original_file.file_name, "file_url": source_file,
                        "is_private": original_file.is_private, "attached_to_doctype": document.doctype,
                        "attached_to_name": document.name, "attached_to_field": "source_file"}).insert()
        row["complementary_item"] = document.name
        result.append(document)
    return result


def validate_review_item(doc, previous=None):
    if previous and previous.get("accounting_source_key"):
        for field in EVIDENCE_FIELDS:
            def normalized(value):
                if field in {"source_debit", "source_credit", "source_fx_rate", "source_row"}:
                    return decimal_value(value)
                return clean_text(value)
            if normalized(doc.get(field)) != normalized(previous.get(field)):
                frappe.throw(_("No se puede modificar la evidencia contable original: {0}.").format(field))
    if doc.category == "Compensación entre partidas":
        doc.review_action = "Compensación entre partidas"
        return  # Paired ledger validation and totals run in the controller.
    if doc.get("review_action") == "Compensación entre partidas":
        frappe.throw(_("Seleccione el concepto Compensación entre partidas."))
    if doc.get("review_action") == "Ajuste de aplicación":
        doc.category = "Ajuste de aplicación"
        return  # Financial and source-link checks run after currency conversion.
    if doc.category == "Ajuste de aplicación":
        frappe.throw(_("Use el tratamiento Ajuste de aplicación para este concepto."))
    if not doc.get("accounting_source_key"):
        if doc.category == "Por clasificar":
            frappe.throw(_("Por clasificar se reserva para movimientos contables importados."))
        return
    action = doc.get("review_action") or "Pendiente de revisión"
    if doc.get("related_application"):
        row = frappe.db.get_value("CN Source Row", doc.related_application,
                                  ["parent", "parenttype", "event_type", "effective"], as_dict=True)
        if not row or row.parenttype != "CN Accounting Import" or row.event_type != "Aplicacion" or not row.effective:
            frappe.throw(_("Seleccione una aplicación contable vigente."))
        parent = frappe.get_doc("CN Accounting Import", row.parent)
        parent.check_permission("read")
        if not doc.employer or parent.employer != doc.employer:
            frappe.throw(_("La aplicación original debe pertenecer a la empresa de la partida."))
        doc.related_import = parent.name
    else:
        doc.related_import = ""
    if action == "Reversión identificada" and not doc.get("related_application"):
        frappe.throw(_("Vincule la aplicación original antes de marcar la reversión como identificada."))
    reviewed = action in {"No conciliatoria", "Reversión identificada", "Partida de depósito"}
    if reviewed and not clean_text(doc.get("review_notes")):
        frappe.throw(_("Indique las observaciones de la revisión."))
    doc.review_status = (action if action in {"No conciliatoria", "Reversión identificada"}
                         else "Pendiente de identificar" if not doc.employer
                         else "Pendiente de revisión")
    if action == "Partida de depósito":
        if doc.accounting_classification in {APPLICATION, DEBIT_NOTE}:
            frappe.throw(_("Una nota de débito o reversión de pago no debe compensarse como partida de depósito. Vincule la aplicación original y revise el ajuste en el core."))
        if not doc.employer or not doc.reference or doc.category == "Por clasificar" or not doc.get("amount_reviewed"):
            frappe.throw(_("Complete empresa, referencia del depósito, concepto y confirme el importe y signo revisados."))
        doc.review_status = "Lista para conciliar"
    if doc.docstatus == 1 and action != "Partida de depósito":
        frappe.throw(_("Este movimiento es solo evidencia en revisión. No se puede confirmar ni afectar saldos como una partida de depósito."))


@frappe.whitelist()
def application_candidates(item_name, accounting_import):
    item = frappe.get_doc("CN Complementary Item", item_name)
    item.check_permission("write")
    parent = frappe.get_doc("CN Accounting Import", accounting_import)
    parent.check_permission("read")
    if not item.employer or parent.employer != item.employer:
        frappe.throw(_("Seleccione una importación de la misma empresa."))
    return [{"name": row.name, "client_name": row.client_name, "loan_number": row.loan_number,
             "event_date": row.event_date, "amount_usd": row.amount_usd, "voucher": row.voucher,
             "application_adjustment_usd": row.application_adjustment_usd, "net_applied_usd": row.net_applied_usd}
            for row in parent.rows if row.event_type == "Aplicacion" and row.effective]
