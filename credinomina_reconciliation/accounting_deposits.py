"""Idempotent draft deposits from the accounting ledger (02 / 12)."""
import frappe
from frappe import _

from credinomina_reconciliation.accounting_review import plan_review_items
from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER, ensure_unidentified_employer
from credinomina_reconciliation.parsers import clean_text, SourceFileError
from credinomina_reconciliation.rounding import decimal_value, money_float

DOCTYPE = "CN Remittance Allocation"
EVIDENCE_FIELDS = (
    "accounting_source_key", "source_account", "source_debit", "source_credit",
    "source_currency", "source_fx_rate", "source_date", "source_voucher",
    "source_description", "source_file", "source_file_hash", "source_row",
    "accounting_reference", "accounting_classification", "tmov", "tdoc", "source_client_name", "source_loan_number",
)


def plan_deposits(records, employers, fallback=""):
    candidates = [dict(row, event_type="Ajuste") for row in records if row.get("event_type") == "Deposito" and row.get("accounting_classification") == "Depósito"]
    result = plan_review_items(candidates, employers, fallback)
    for row in result:
        row["event_type"] = "Deposito"
        currency = row.get("bank_deposit_currency") or row["source_currency"]
        amount = row.get("bank_deposit_amount") or row["source_credit"]
        rate = row.get("source_fx_rate") or 0
        if not row.get("event_date") or not row.get("bank_deposit_reference"):
            raise SourceFileError(f"Fila {row['source_row']}: el depósito necesita fecha y referencia bancaria.")
        if currency == "NIO" and decimal_value(rate) <= 0:
            raise SourceFileError(f"Fila {row['source_row']}: el depósito en C$ necesita una tasa C$/US$; importe el archivo contable en NIO con su tasa.")
        if fallback and row.get("resolved_employer") and row["resolved_employer"] not in {fallback, UNIDENTIFIED_EMPLOYER} and not row.get("_manual_employer"):
            raise SourceFileError(f"Fila {row['source_row']}: el depósito corresponde a otra empresa; use carga masiva sin empresa predeterminada.")
        row.update(deposit_date=row.get("bank_deposit_date") or row["event_date"],
                   deposit_currency=currency, deposit_amount=money_float(amount), deposit_fx_rate=rate,
                   deposit_usd=money_float(decimal_value(amount) / decimal_value(rate) if currency == "NIO" else amount))
    # Conflicting currencies for the same printed bank identifier in one file
    # cannot establish a reliable new bank account, regardless of row order.
    currencies = {}
    for row in result:
        key = (row.get("bank_name_hint"), row.get("bank_number_hint"))
        currencies.setdefault(key, set()).add(row.get("bank_currency_hint"))
    for row in result:
        if len(currencies[(row.get("bank_name_hint"), row.get("bank_number_hint"))]) > 1:
            row["bank_name_hint"] = ""
    return result


def _bank_account(row):
    bank, number = row.get("bank_name_hint"), row.get("bank_number_hint")
    currency = row["deposit_currency"]
    if not bank or not number or row.get("bank_currency_hint") != currency:
        return "", "Cuenta bancaria no identificada con certeza; complete manualmente."
    if not frappe.has_permission("CN Bank Account", "read"):
        return "", "Sin permiso para identificar la cuenta bancaria."
    matches = frappe.get_list("CN Bank Account", filters={"bank_name": bank},
                              fields=["name", "account_number", "currency", "active"], limit_page_length=0)
    normalized = number.replace("-", "")
    # A short account identifier may be the suffix printed by accounting. Only
    # reuse it if it points to a single account, never create a second suffix copy.
    matches = [account for account in matches if clean_text(account.account_number).replace("-", "").endswith(normalized)]
    if len(matches) == 1 and matches[0].active and matches[0].currency == currency:
        return matches[0].name, "Cuenta identificada por banco, número y moneda."
    if matches:
        return "", "Cuenta ambigua, inactiva o con otra moneda; seleccione manualmente."
    if not frappe.has_permission("CN Bank Account", "create"):
        return "", "Cuenta identificada pero sin permiso para crearla; complete manualmente."
    name = f"{bank} {number} {'C$' if currency == 'NIO' else 'US$'}"
    if frappe.db.exists("CN Bank Account", name):
        return "", "Nombre de cuenta existente con datos diferentes; revise manualmente."
    account = frappe.get_doc({"doctype": "CN Bank Account", "account_name": name,
        "bank_name": bank, "account_number": number, "currency": currency,
        "notes": "Creada desde identificación explícita en movimiento contable. Verifique si el número es un identificador abreviado."}).insert()
    return account.name, "Cuenta creada desde banco, identificador y moneda explícitos."


def create_deposits(records, source_file, file_hash):
    if not records:
        return []
    if not frappe.has_permission(DOCTYPE, "create"):
        frappe.throw(_("Se requiere permiso para crear depósitos."), frappe.PermissionError)
    if any(row.get("resolved_employer") == UNIDENTIFIED_EMPLOYER for row in records):
        ensure_unidentified_employer()
    result = []
    # Also serializes creation of previously unknown bank accounts.
    from credinomina_reconciliation.accounting_batch_store import creation_guard
    with creation_guard():
        for row in records:
            existing = frappe.db.get_value(DOCTYPE, {"accounting_source_key": row["accounting_source_key"]}, "name", for_update=True)
            if existing:
                document = frappe.get_doc(DOCTYPE, existing)
                document.check_permission("read")
                if document.source_currency != row["source_currency"] or decimal_value(document.source_fx_rate) != decimal_value(row.get("source_fx_rate")):
                    frappe.throw(_("El depósito {0} ya tiene una moneda/tasa de importación distinta; revise su evidencia antes de reprocesar.").format(existing))
                row["remittance_allocation"] = existing
                continue
            if frappe.db.exists("CN Complementary Item", {"accounting_source_key": row["accounting_source_key"]}):
                frappe.throw(_("Fila {0}: este movimiento ya se registró como partida complementaria. Revise ese registro antes de importarlo como depósito para no duplicar su efecto.").format(row["source_row"]))
            if row.get("resolved_employer"):
                frappe.get_doc("CN Employer", row["resolved_employer"]).check_permission("read")
            duplicates = frappe.get_all(DOCTYPE, filters={"docstatus": ["!=", 2],
                "deposit_reference": row["bank_deposit_reference"], "deposit_date": row["deposit_date"],
                "deposit_currency": row["deposit_currency"], "deposit_amount": row["deposit_amount"]}, pluck="name")
            bank, bank_reason = _bank_account(row)
            notes = ["Importado de contabilidad. Revise y confirme el depósito; no se ha conciliado.", bank_reason]
            if duplicates:
                notes.append("Posible repetición: ya existe un depósito con referencia, fecha, moneda e importe iguales. Se conservó esta línea en borrador; revise antes de confirmar para no duplicar efectivo.")
            if not row.get("resolved_employer") or row["resolved_employer"] == UNIDENTIFIED_EMPLOYER:
                notes.append("Empresa pendiente de identificar: " + (row.get("employer_text") or "Sin dato"))
            if not row.get("bank_deposit_currency"):
                notes.append("Sin importe bancario explícito: se usó el crédito y moneda contables; verifique el depósito completo.")
            notes.append(row.get("bank_deposit_notes") or "")
            document = frappe.get_doc({"doctype": DOCTYPE,
                "employer": row.get("resolved_employer") or "", "bank_account": bank,
                "deposit_date": row["deposit_date"], "deposit_reference": row["bank_deposit_reference"],
                "deposit_voucher": row.get("voucher"), "deposit_currency": row["deposit_currency"],
                "deposit_amount": row["deposit_amount"], "fx_rate": row["deposit_fx_rate"],
                "notes": "\n".join(filter(None, notes)), "bank_identification": bank_reason,
                **{field: row.get(field) for field in EVIDENCE_FIELDS if field in row},
                "source_date": row["event_date"], "source_voucher": row.get("voucher"),
                "source_client_name": row.get("client_name") or "", "source_loan_number": row.get("loan_number") or "",
                "source_file": source_file, "source_file_hash": file_hash,
            }).insert()
            from credinomina_reconciliation.file_references import attach_existing_file
            attach_existing_file(source_file, DOCTYPE, document.name, "source_file")
            row["remittance_allocation"] = document.name
            result.append(document)
    return result


def validate_evidence(doc, previous):
    if not previous or not previous.get("accounting_source_key"):
        return
    for field in EVIDENCE_FIELDS:
        normalize = decimal_value if field in {"source_debit", "source_credit", "source_fx_rate", "source_row"} else clean_text
        if normalize(doc.get(field)) != normalize(previous.get(field)):
            frappe.throw(_("No se puede modificar la evidencia contable original: {0}.").format(field))
