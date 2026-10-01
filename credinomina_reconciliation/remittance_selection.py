"""Read-only pending destinations for the deposit picker (amounts in USD)."""

import json
from collections import defaultdict

from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.tolerance_items import CATEGORY as TOLERANCE_CATEGORY


def target_key(row):
    if row.get("historical_application") or row.get("aplicacion_id"):
        return ("H", row.get("historical_application") or row["aplicacion_id"])
    if row.get("complementary_item") or row.get("partida"):
        return ("X", row.get("complementary_item") or row["partida"])
    if row.get("row_key") or row.get("fila_id"):
        return ("C", row.get("period") or row.get("periodo"),
                row.get("row_key") or row.get("fila_id"))
    return None


def pending_selection(candidates, deposits, current_name, amount_usd, targets):
    """Do not double count manual instructions already reflected in allocations.

    Automatic cash allocations also consume the deposit. Drafts/cancelled
    deposits do not pay claims. Existing form targets are never replaced.
    """
    paid = defaultdict(lambda: money(0))
    current_paid = defaultdict(lambda: money(0))
    other_current_cash = money(0)
    for deposit in deposits:
        if deposit.get("docstatus") != 1:
            continue
        detail = deposit.get("allocation_detail") or []
        if isinstance(detail, str):
            detail = json.loads(detail)
        for entry in detail:
            key = target_key(entry)
            amount = money(entry.get("importe_usd"))
            if key:
                paid[key] += amount
            if deposit["name"] == current_name:
                if key:
                    current_paid[key] += amount
                else:
                    other_current_cash += amount
    manual = defaultdict(lambda: money(0))
    for row in targets:
        key = target_key(row)
        # Even an incomplete manual row consumes budget until the user edits it.
        manual[key] += money(row.get("amount_usd"))
    reserved = other_current_cash + sum(
        ((min if min(current_paid[key], manual[key]) < 0 else max)(current_paid[key], manual[key])
         for key in current_paid.keys() | manual.keys()),
        money(0),
    )
    available = max(money(amount_usd) - reserved, money(0))
    rows = []
    for candidate in candidates:
        key = target_key(candidate)
        if key in manual:
            continue
        pending = max(money(candidate["due_usd"]) - paid[key], money(0))
        if pending > 0:
            rows.append({**candidate, "id": json.dumps(key, ensure_ascii=False),
                         "applied_cents": (int(money(candidate["applied_usd"]) * 100)
                                           if candidate.get("applied_usd") is not None else None),
                         "assigned_cents": int(paid[key] * 100),
                         "pending_cents": int(pending * 100)})
    return {"rows": rows, "available_cents": int(available * 100),
            "reserved_cents": int(reserved * 100)}


def get_pending_targets(remittance_name, targets=None):
    import frappe
    from frappe import _
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
        _allocate_complementary_items, _deducted_amount,
    )

    doc = frappe.get_doc("CN Remittance Allocation", remittance_name)
    doc.check_permission("write")
    if doc.docstatus == 2:
        frappe.throw(_("El depósito está cancelado."))
    doc._assert_open_related_periods()
    if not doc.employer:
        frappe.throw(_("Seleccione la empresa y guarde el depósito primero."))
    targets = frappe.parse_json(targets) if isinstance(targets, str) else targets
    if targets is None:
        targets = [row.as_dict() for row in doc.targets]
    if not isinstance(targets, list) or any(not isinstance(row, dict) for row in targets):
        frappe.throw(_("La lista de destinos no es válida."))

    # get_list applies user permissions; child tables are read through their parents.
    periods = [frappe.get_doc("CN Reconciliation Period", row.name) for row in frappe.get_list(
        "CN Reconciliation Period", filters={"employer": doc.employer},
        fields=["name"], order_by="payroll_month asc, name asc", limit_page_length=0,
    )]
    periods = [period for period in periods if period.has_permission("read")]
    open_periods = {p.name: p for p in periods if p.status != "Cerrado"}
    # Internal accounting totals must include allocations hidden by user permissions.
    deposits = frappe.get_all(
        "CN Remittance Allocation", filters={"docstatus": 1, "employer": doc.employer},
        fields=["name", "docstatus", "allocation_detail"], limit_page_length=0,
    )
    items = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "category": ["not in", ["Saldo a favor de la empresa", TOLERANCE_CATEGORY]]},
        fields=["name", "reference", "amount_usd", "employer", "period",
                "client_number", "loan_number", "installment_number", "description", "voucher"],
        limit_page_length=0)
    linked_items = _allocate_complementary_items(items, periods)
    complementary = defaultdict(lambda: money(0))
    for (row_name, _reference), linked in linked_items.items():
        complementary[row_name] += sum((money(item.amount_usd) for item in linked), money(0))

    def period_label(period):
        dates = (display_date(period.historical_application_date)
                 if period.historical_scope == "Fecha exacta" else
                 " – ".join(display_date(d) for d in [period.historical_start_date, period.historical_end_date] if d)
                 if period.historical_scope == "Rango de fechas" else
                 display_date(period.cutoff_date or period.payroll_month))
        return f"{dates} · {period.name}"

    def identity(row):
        return {key: row.get(key) or "" for key in (
            "client_name", "client_number", "employee_number", "national_id", "loan_number",
        )}

    candidates = []
    for period in open_periods.values():
        if period.reconciliation_mode == "Historica":
            continue
        for row in period.collection_rows:
            if not row.row_key:
                continue
            candidates.append({**identity(row), "kind": "Cobranza", "period": period.name,
                "period_label": period_label(period), "filter_period": period.name,
                "row_key": row.row_key, "reference": row.application_reference or "",
                "applied_usd": float(money(row.applied_usd)),
                "due_usd": float(max(money(_deducted_amount(row, "USD")) - complementary[row.name]
                                     + min(money(row.rounding_adjustment_usd), money(0)), money(0)))})
    if open_periods and frappe.has_permission("CN Accounting Import", "read"):
        parents = frappe.get_list("CN Accounting Import",
            filters={"status": ["in", ["Importado", "Importado con excepciones"]]},
            pluck="name", limit_page_length=0)
        for parent in parents:
            source = frappe.get_doc("CN Accounting Import", parent)
            if not source.has_permission("read"):
                continue
            for row in source.rows:
                period = open_periods.get(row.historical_period)
                if (not period or period.reconciliation_mode != "Historica"
                        or row.event_type != "Aplicacion" or not row.effective
                        or row.currency != "USD" or row.match_status != "Conciliado"):
                    continue
                candidates.append({**identity(row), "kind": "Aplicación histórica",
                    "historical_application": row.name, "filter_period": period.name,
                    "period_label": period_label(period),
                    "applied_usd": float(money(row.amount)),
                    "reference": " · ".join(str(v) for v in [row.reference, row.voucher, row.receipt] if v),
                    "due_usd": float(money(row.amount) + min(money(row.rounding_adjustment_usd), money(0)))})
    for item in items:
        period = open_periods.get(item.period)
        if item.period and not period:
            continue
        if (item.employer or (period.employer if period else None)) != doc.employer:
            continue
        if not frappe.has_permission("CN Complementary Item", "read", doc=item.name):
            continue
        candidates.append({**identity(item), "kind": "Partida complementaria",
            "client_name": item.description, "complementary_item": item.name,
            "filter_period": item.period or "", "period_label": period_label(period) if period else "Sin período",
            "reference": " · ".join(str(v) for v in [item.reference, item.voucher] if v),
            "due_usd": float(money(item.amount_usd))})
    result = pending_selection(candidates, deposits, doc.name, doc.amount_usd, targets)
    result["employer"] = doc.employer
    result["modified"] = str(doc.modified)
    return result
