"""Restore overwritten historical statuses using both sides of saved cash evidence."""
import json
from collections import defaultdict
from decimal import InvalidOperation

import frappe

from credinomina_reconciliation.reconciliation import net_application_amount
from credinomina_reconciliation.rounding import money


OVERWRITTEN_REASON = "La aplicacion aun no se enlaza de forma unica con una cobranza."


def _entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value or []
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed) else None


def deposit_evidence(deposits):
    evidence = defaultdict(list)
    for deposit in deposits:
        if deposit.get("docstatus") != 1:
            continue
        for entry in _entries(deposit.get("allocation_detail")) or []:
            if entry.get("tipo") != "Aplicacion historica" or not entry.get("aplicacion_id"):
                continue
            evidence[entry["aplicacion_id"]].append({
                "period": entry.get("periodo"), "employer": deposit.get("employer"),
                "reference": deposit.get("deposit_reference") or "",
                "voucher": deposit.get("deposit_voucher") or "",
                "date": str(deposit.get("deposit_date") or ""),
                "amount": entry.get("importe_usd"),
            })
    return evidence


def verified_status(row, period, employer, evidence):
    """Conservative repair: only fully covered, unadjusted historical rows."""
    try:
        return _verified_status(row, period, employer, evidence)
    except (InvalidOperation, TypeError, ValueError):
        return None  # Malformed legacy evidence must remain available for review.


def _verified_status(row, period, employer, evidence):
    if (row.get("event_type") != "Aplicacion" or not row.get("effective")
            or row.get("match_status") != "Conciliado"
            or row.get("deposit_match_status") not in {"Sin deposito", "Ambiguo"}
            or row.get("deposit_match_reason") != OVERWRITTEN_REASON
            or not period or period.get("reconciliation_mode") != "Historica"
            or period.get("employer") != employer
            or row.get("historical_period") != period.get("name")):
        return None
    # Adjusted cases require their own confirmed movement evidence. Never infer
    # a settlement just from zero balance, a closed period or a matching total.
    if money(row.get("rounding_adjustment_usd")) or money(row.get("application_adjustment_usd")):
        return None
    applied = money(net_application_amount(row))
    if (applied <= 0 or money(row.get("historical_remitted_usd")) != applied
            or money(row.get("historical_balance_usd")) != 0):
        return None
    details = _entries(row.get("historical_detail"))
    if not details or not evidence:
        return None
    stored, confirmed = defaultdict(lambda: money(0)), defaultdict(lambda: money(0))
    for item in details:
        if item.get("movimiento") or money(item.get("diferencia_usd")) or money(item.get("importe_usd")) <= 0:
            return None
        key = (item.get("referencia") or "", item.get("comprobante") or "", str(item.get("fecha") or ""))
        stored[key] += money(item.get("importe_usd"))
    for item in evidence:
        if (item["period"] != period["name"] or item["employer"] != employer
                or money(item["amount"]) <= 0):
            return None
        confirmed[(item["reference"], item["voucher"], item["date"])] += money(item["amount"])
    if stored != confirmed or sum(confirmed.values()) != applied:
        return None
    return {
        "deposit_match_status": "Depósito conciliado",
        "deposit_match_reason": (
            f"Aplicación histórica: {applied:.2f} US$ respaldados por distribuciones "
            "guardadas de depósitos confirmados; saldo pendiente 0.00 US$. "
            "Estado restaurado sin modificar importes ni vínculos."
        ),
    }


def repair_historical_statuses(dry_run=True):
    """No reimport, reconciliation, save or commit; safe for closed periods."""
    from frappe.utils import cint
    dry_run = bool(cint(dry_run))
    candidates = frappe.get_all("CN Source Row", filters={
        "parenttype": "CN Accounting Import", "parentfield": "rows", "event_type": "Aplicacion",
        "effective": 1, "match_status": "Conciliado", "historical_period": ["is", "set"],
        "deposit_match_status": ["in", ["Sin deposito", "Ambiguo"]],
        "deposit_match_reason": OVERWRITTEN_REASON,
    }, fields=["parent"], limit_page_length=0)
    result = {"dry_run": dry_run, "rows_repaired": 0, "imports_repaired": 0, "unverified_rows": []}
    cached_employer, evidence = object(), {}
    for name in sorted({row.parent for row in candidates}):
        document = frappe.get_doc("CN Accounting Import", name)
        if document.docstatus == 2 or document.status not in {"Importado", "Importado con excepciones"}:
            continue
        period_names = sorted({row.historical_period for row in document.rows if row.historical_period})
        periods = {period.name: period for period in frappe.get_all("CN Reconciliation Period",
            filters={"name": ["in", period_names]}, fields=["name", "employer", "reconciliation_mode"],
            limit_page_length=0)}
        if cached_employer != document.employer:
            deposits = frappe.get_all("CN Remittance Allocation", filters={"employer": document.employer, "docstatus": 1},
                fields=["name", "employer", "docstatus", "deposit_reference", "deposit_voucher", "deposit_date", "allocation_detail"],
                limit_page_length=0)
            evidence = deposit_evidence(deposits)
            cached_employer = document.employer
        repaired = 0
        for row in document.rows:
            if (row.deposit_match_reason != OVERWRITTEN_REASON
                    or row.deposit_match_status not in {"Sin deposito", "Ambiguo"}
                    or not row.historical_period):
                continue
            changes = verified_status(row, periods.get(row.historical_period), document.employer, evidence.get(row.name))
            if not changes:
                result["unverified_rows"].append({"import": name, "row": row.name})
                continue
            repaired += 1
            row.update(changes)
            if not dry_run:
                frappe.db.set_value("CN Source Row", row.name, changes, update_modified=False)
        if repaired:
            result["rows_repaired"] += repaired
            result["imports_repaired"] += 1
            if not dry_run:
                from credinomina_reconciliation.patches.v1_0.refresh_accounting_import_summaries import SUMMARY_FIELDS
                document.recalculate_reconciliation_summary()
                frappe.db.set_value(document.doctype, name,
                    {field: document.get(field) for field in SUMMARY_FIELDS}, update_modified=False)
                frappe.clear_document_cache(document.doctype, name)
    return result
