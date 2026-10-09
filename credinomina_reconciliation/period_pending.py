"""Read-only detected issues, distinct from registered follow-up exceptions."""
import json

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.control_summary import readable_imports
from credinomina_reconciliation.collection_identity import collection_client_number
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.application_quality import collection_quality
from credinomina_reconciliation.remittance_periods import selected_periods, attach_periods, deposit_names_for_periods


SETTLED = {"Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"}


def application_issue(row):
    first_pending = (row.get("processing_route") == "Operativa"
                     and not str(row.get("quality_status") or "").startswith("Conforme"))
    if row.get("deposit_match_status") in SETTLED and not first_pending:
        return None
    status = row.get("deposit_match_status") or "Pendiente"
    reason = row.get("deposit_match_reason") or row.get("match_reason") or _("Aplicación pendiente de conciliar.")
    applied = money(row.get("net_applied_usd"))
    paid = money(row.get("historical_remitted_usd"))
    pending = money(row.get("historical_balance_usd"))
    # Older reconciliations can retain the operative "Sin deposito" reason
    # even though historical cash coverage has already been recorded.
    if paid > 0 and pending > 0:
        status = "Depósito parcial"
        reason = _(
            "La aplicación tiene US$ {0} vinculados a depósitos de un aplicado neto de US$ {1}. "
            "Quedan US$ {2} pendientes de cubrir."
        ).format(f"{paid:,.2f}", f"{applied:,.2f}", f"{pending:,.2f}")
    return {
        "kind": "Aplicación", "source": row.get("parent"), "doctype": "CN Accounting Import",
        "source_row": row.get("name"), "installment_number": row.get("installment_number"),
        "row": row.get("idx"), "client_name": row.get("client_name"),
        "client_number": row.get("client_number"), "loan_number": row.get("loan_number"),
        "applied": money_float(applied),
        "paid": money_float(paid),
        "pending": money_float(pending),
        "status": "Conciliación 1 pendiente" if first_pending else status,
        "reason": row.get("quality_status") if first_pending else reason,
    }


def collection_issue(row, basis=None, payments=()):
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
    identified = sum((money(payment['amount_usd']) for payment in payments), money(0))
    pending_deposit = pending
    if identified > 0:
        status = _("Pago pendiente de aplicar")
        reasons.append(_("US$ {0} identificados en el detalle de depósitos confirmados, pendientes de aplicar en el core. No son saldo a favor ni una distribución ya conciliada.").format(f"{identified:,.2f}"))
        paid += identified
        pending = identified
    if not reasons:
        return None
    return {
        "kind": "Cobranza", "source": row.get("parent"), "doctype": "CN Reconciliation Period",
        "source_row": row.get("name"), "installment_number": row.get("installment_number"),
        "row": row.get("idx"), "client_name": row.get("client_name"),
        "client_number": collection_client_number(row), "loan_number": row.get("loan_number"),
        "applied": money_float(applied), "paid": money_float(paid), "pending": money_float(pending),
        "paid_identified": money_float(identified), "pending_application": money_float(identified),
        "pending_deposit": money_float(pending_deposit),
        "pending_label": _("Por aplicar") if identified > 0 else _("Por cubrir con depósito"),
        "can_create_complementary": not bool(identified),
        "deposit_evidence": [{"name": payment['target_name'], "detail_row": payment.get('deposit_detail_row'),
                              "amount_usd": payment['amount_usd']} for payment in payments],
        "status": status, "reason": " · ".join(reasons),
    }


def period_payment_evidence(period, deposits):
    """Use the same evidence as Control; include other linked periods to avoid guessing."""
    from credinomina_reconciliation.deposit_reconciliation import entries
    from credinomina_reconciliation.pending_payments import load_pending_payments

    scopes = {deposit.name: set(selected_periods(deposit)) | {
        entry.get('periodo') for entry in entries(deposit.allocation_detail) if entry.get('periodo')
    } for deposit in deposits}
    other_names = set().union(*scopes.values()) - {period.name} if scopes else set()
    periods = [{'name': period.name, 'employer': period.employer,
                'reconciliation_mode': period.reconciliation_mode, 'application_basis': period.get('application_basis')}]
    if other_names:
        periods.extend(frappe.get_list('CN Reconciliation Period', filters={'name': ['in', sorted(other_names)]},
            fields=['name', 'employer', 'reconciliation_mode', 'application_basis'], limit_page_length=0))
    visible = {item['name'] for item in periods}
    readable = [deposit for deposit in deposits if scopes[deposit.name] <= visible]
    tasks = load_pending_payments(periods, readable)
    return [task for task in tasks if task['period'] == period.name], len(readable) != len(deposits)


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


def load_period_deposits(period_name):
    """Confirmed, readable deposits with an exact link to this period."""
    needle = json.dumps(period_name, ensure_ascii=False).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    deposits = frappe.get_list("CN Remittance Allocation", filters={"docstatus": 1},
        or_filters=[["name", "in", deposit_names_for_periods([period_name]) or [""]], ["allocation_detail", "like", f"%{needle}%"]],
        fields=["name", "employer", "allocation_detail", "amount_usd", "unclassified_usd", "result", "detail_status"],
        order_by="deposit_date asc, name asc", limit_page_length=0)
    return [deposit for deposit in attach_periods(deposits) if deposit_issue(deposit, period_name)]


@frappe.whitelist()
def get_period_pending(period_name, start=0, search=None, kind=None):
    period = frappe.get_doc("CN Reconciliation Period", period_name)
    period.check_permission("read")
    issues, restricted = [], []
    if frappe.has_permission("CN Accounting Import", "read"):
        rows = frappe.get_all("CN Source Row", filters={
            "historical_period": period.name, "parenttype": "CN Accounting Import",
            "event_type": "Aplicacion", "effective": 1,
        }, fields=["name", "parent", "idx", "client_name", "client_number", "loan_number", "installment_number", "net_applied_usd",
                   "historical_remitted_usd", "historical_balance_usd", "deposit_match_status",
                   "deposit_match_reason", "match_reason", "processing_route", "quality_status"], order_by="parent asc, idx asc", limit_page_length=0)
        allowed = readable_imports({row.parent for row in rows})
        if any(row.parent not in allowed for row in rows):
            restricted.append("CN Accounting Import")
        issues.extend(issue for row in rows if row.parent in allowed if (issue := application_issue(row)))
    else:
        restricted.append("CN Accounting Import")
    deposit_issues, payments = [], []
    if frappe.has_permission("CN Remittance Allocation", "read"):
        linked_deposits = load_period_deposits(period.name)
        deposit_issues = [deposit_issue(deposit, period.name) for deposit in linked_deposits]
        if period.reconciliation_mode != "Historica" and linked_deposits:
            payments, partial = period_payment_evidence(period, linked_deposits)
            if partial:
                restricted.append("CN Reconciliation Period")
    else:
        restricted.append("CN Remittance Allocation")
    if period.reconciliation_mode != "Historica":
        from collections import defaultdict
        by_collection = defaultdict(list)
        for payment in payments:
            by_collection[payment['collection_row']].append(payment)
        issues.extend(issue for row in period.collection_rows if (issue := collection_issue(
            row, period.get("application_basis"), by_collection[row.name])))
    issues.extend(deposit_issues)
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
            "restricted": restricted, "start": start,
            "can_create_complementary": period.status != "Cerrado" and period.docstatus != 2
                and bool(frappe.has_permission("CN Complementary Item", "create"))}
