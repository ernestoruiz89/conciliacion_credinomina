"""Read-only detected issues, distinct from registered follow-up exceptions."""
import json

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.control_summary import readable_imports
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.remittance_periods import selected_periods, attach_periods, deposit_names_for_periods


SETTLED = {"Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"}


def application_issue(row):
    if row.get("deposit_match_status") in SETTLED:
        return None
    return {
        "kind": "Aplicación", "source": row.get("parent"), "doctype": "CN Accounting Import",
        "row": row.get("idx"), "client_name": row.get("client_name"),
        "client_number": row.get("client_number"), "loan_number": row.get("loan_number"),
        "applied": money_float(row.get("net_applied_usd")),
        "paid": money_float(row.get("historical_remitted_usd")),
        "pending": money_float(row.get("historical_balance_usd")),
        "status": row.get("deposit_match_status") or "Pendiente",
        "reason": row.get("deposit_match_reason") or row.get("match_reason") or _("Aplicación pendiente de conciliar."),
    }


def collection_issue(row, basis=None):
    reasons = []
    quality = collection_quality(row, basis)
    if not quality["quality_status"].startswith("Conforme"):
        reasons.append(_(quality["quality_status"]))
    applied, paid = money(row.get("applied_usd")), money(row.get("remitted_usd"))
    pending = max(applied + money(row.get("complementary_usd"))
                  + money(row.get("rounding_adjustment_usd")) - paid, money(0))
    status = row.get("application_status") or "Pendiente"
    if status != "Aplicado y remitido":
        reasons.append(status)
    if pending > 0:
        reasons.append(_("Aplicación pendiente de cubrir con depósito."))
    if not reasons:
        return None
    return {
        "kind": "Cobranza", "source": row.get("parent"), "doctype": "CN Reconciliation Period",
        "row": row.get("idx"), "client_name": row.get("client_name"),
        "client_number": row.get("client_number"), "loan_number": row.get("loan_number"),
        "applied": money_float(applied), "paid": money_float(paid), "pending": money_float(pending),
        "status": status, "reason": " · ".join(reasons),
    }


def deposit_issue(deposit, period_name):
    try:
        entries = json.loads(deposit.get("allocation_detail") or "[]")
    except (ValueError, TypeError):
        entries = []
    if not isinstance(entries, list):
        entries = []
    linked = period_name in selected_periods(deposit) or (
        isinstance(entries, list) and any(isinstance(entry, dict) and entry.get("periodo") == period_name
                                        for entry in entries)
    )
    if not linked or (money(deposit.get("unclassified_usd")) <= 0 and deposit.get("result") == "Conciliado"):
        return None
    # Only executed credit allocations, across all periods of this deposit.
    # Planned targets, complementary items and tolerance cash are not core payments.
    credit_allocated = sum((money(entry.get("importe_usd")) for entry in entries
                            if isinstance(entry, dict)
                            and entry.get("tipo") in {"Cobranza", "Aplicacion historica"}), money(0))
    return {
        "kind": "Depósito", "source": deposit.get("name"), "doctype": "CN Remittance Allocation",
        "row": None, "client_name": None, "client_number": None, "loan_number": None,
        "applied": money_float(credit_allocated), "paid": money_float(deposit.get("amount_usd")),
        "pending": money_float(deposit.get("unclassified_usd")),
        "status": deposit.get("result") or "Pendiente",
        "reason": _("Aplicado neto: importe del depósito asignado a créditos de todos sus períodos, sin partidas complementarias ni ajustes de conciliación. No es el total original de las aplicaciones vinculadas.")
            + " " + _("Saldo sin distribuir del depósito completo; puede corresponder a otros períodos. No se suma al pendiente de las aplicaciones.")
            + " " + (deposit.get("detail_status") or ""),
    }


@frappe.whitelist()
def get_period_pending(period_name, start=0, search=None, kind=None):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    issues, restricted = [], []
    if period.reconciliation_mode == "Historica":
        if frappe.has_permission("CN Accounting Import", "read"):
            rows = frappe.get_all("CN Source Row", filters={
                "historical_period": period.name, "parenttype": "CN Accounting Import",
                "event_type": "Aplicacion", "effective": 1,
            }, fields=["parent", "idx", "client_name", "client_number", "loan_number", "net_applied_usd",
                       "historical_remitted_usd", "historical_balance_usd", "deposit_match_status",
                       "deposit_match_reason", "match_reason"], order_by="parent asc, idx asc", limit_page_length=0)
            allowed = readable_imports({row.parent for row in rows})
            if any(row.parent not in allowed for row in rows):
                restricted.append("CN Accounting Import")
            issues.extend(issue for row in rows if row.parent in allowed if (issue := application_issue(row)))
        else:
            restricted.append("CN Accounting Import")
    else:
        issues.extend(issue for row in period.collection_rows if (issue := collection_issue(row, period.get("application_basis"))))

    if frappe.has_permission("CN Remittance Allocation", "read"):
        # Narrow to this period, then verify the exact JSON link (LIKE is only a prefilter).
        needle = json.dumps(period.name, ensure_ascii=False).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        deposits = frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1},
            or_filters=[["name", "in", deposit_names_for_periods([period.name]) or [""]], ["allocation_detail", "like", f"%{needle}%"]],
            fields=["name", "allocation_detail", "amount_usd", "unclassified_usd", "result", "detail_status"],
            order_by="deposit_date asc, name asc", limit_page_length=0)
        issues.extend(issue for deposit in attach_periods(deposits) if (issue := deposit_issue(deposit, period.name)))
    else:
        restricted.append("CN Remittance Allocation")
    total = len(issues)
    if kind:
        issues = [issue for issue in issues if issue["kind"] == kind]
    if search:
        import unicodedata
        def normalized(value):
            return "".join(c for c in unicodedata.normalize("NFD", str(value or "").casefold())
                           if not unicodedata.combining(c))
        terms = normalized(search).split()
        issues = [issue for issue in issues if all(term in normalized(" ".join(str(value or "") for value in issue.values())) for term in terms)]
    start = max(cint(start), 0)
    return {"rows": issues[start:start + 50], "count": len(issues), "total": total,
            "restricted": restricted, "start": start}
