"""Read-only pending destinations for the deposit picker (amounts in USD)."""

import json
from collections import defaultdict

from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.date_display import display_date
from credinomina_reconciliation.tolerance_items import CATEGORY as TOLERANCE_CATEGORY
from credinomina_reconciliation.reconciliation import net_application_amount
from credinomina_reconciliation.paying_employers import allowed_employers, reconciliation_companies
from credinomina_reconciliation.allocation_origin import DETAIL, FIFO, REFERENCE


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
    automatic_current_cash = money(0)
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
                if key and entry.get("origen") in {DETAIL, FIFO, REFERENCE}:
                    # A new manual target can supplement this automatic payment.
                    # Both consume cash, even when they address the same claim.
                    automatic_current_cash += amount
                elif key:
                    current_paid[key] += amount
                else:
                    other_current_cash += amount
    manual = defaultdict(lambda: money(0))
    for row in targets:
        key = target_key(row)
        # Even an incomplete manual row consumes budget until the user edits it.
        manual[key] += money(row.get("amount_usd"))
    reserved = other_current_cash + automatic_current_cash
    for key in current_paid.keys() | manual.keys():
        assigned = current_paid.get(key, money(0))
        instructed = manual.get(key, money(0))
        reserved += min(assigned, instructed) if min(assigned, instructed) < 0 else max(assigned, instructed)
    available = max(money(amount_usd) - reserved, money(0))
    rows = []
    for candidate in candidates:
        key = target_key(candidate)
        # Only explicit form targets are already selected. Reading a missing
        # defaultdict key while computing cash must not mark automatic claims
        # as selected and hide their outstanding balance.
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


def _historical_sources(open_periods, selected_keys=None):
    """Load only imports containing eligible applications in visible open periods.

    Keep both parent list permissions and document read checks. Do not scan every
    accounting import (and all its child rows) just to discard it afterward.
    """
    import frappe

    historical = list(open_periods)
    if not historical or not frappe.has_permission("CN Accounting Import", "read"):
        return
    filters = {"parenttype": "CN Accounting Import", "parentfield": "rows",
               "historical_period": ["in", historical], "event_type": "Aplicacion",
               "effective": 1, "currency": "USD", "match_status": "Conciliado"}
    if selected_keys is not None:
        names = [key[1] for key in selected_keys if key[0] == "H"]
        if not names:
            return
        filters["name"] = ["in", names]
    parents = frappe.get_all("CN Source Row", filters=filters, pluck="parent",
                             group_by="parent", limit_page_length=0)
    if not parents:
        return
    for parent in frappe.get_list("CN Accounting Import", filters={
        "name": ["in", parents], "status": ["in", ["Importado", "Importado con excepciones"]],
    }, pluck="name", limit_page_length=0):
        source = frappe.get_doc("CN Accounting Import", parent)
        if source.has_permission("read"):
            yield source


def get_pending_targets(remittance_name, targets=None, detail_row_name=None, selected_ids=None):
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
    selected_keys = None
    if selected_ids is not None:
        try:
            ids = json.loads(selected_ids) if isinstance(selected_ids, str) else selected_ids
            if not isinstance(ids, list):
                raise ValueError
            keys = [json.loads(value) for value in ids]
            if any(not isinstance(key, list) or not key or key[0] not in {"C", "H", "X"}
                   or len(key) != (3 if key[0] == "C" else 2)
                   or any(not isinstance(value, str) or not value for value in key) for key in keys):
                raise ValueError
            selected_keys = {tuple(key) for key in keys}
        except (ValueError, TypeError):
            frappe.throw(_("La selección de destinos no es válida."))

    def requested(row):
        return selected_keys is None or target_key(row) in selected_keys

    # get_list applies user permissions; child tables are read through their parents.
    allowed = sorted(allowed_employers(doc.employer))
    detail_row = None
    detail_client = None
    client_catalog = None
    if detail_row_name:
        detail_row = next((row for row in doc.detail_rows if row.name == detail_row_name), None)
        if not detail_row:
            frappe.throw(_("La fila del detalle no pertenece a este depósito."))
        from credinomina_reconciliation.client_registry import load_client_index
        from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
        from credinomina_reconciliation.paying_employers import choose_detail_client

        client_catalog = load_client_index(employers=allowed)
        row_identity = detail_row.as_dict()
        linked_client = next((client for client in client_catalog
                              if client["name"] == detail_row.get("client")), None)
        if linked_client:
            # Client links were established by the import/identity workflow and
            # are authoritative; core and SIAF client numbers can differ.
            detail_client, reason = linked_client, "Cliente vinculado en la fila"
        else:
            identity_fields = ("client_number", "national_id", "employee_number", "client_name")
            if any(row_identity.get(field) for field in identity_fields):
                # Match the customer independently of a credit number that may
                # need correction on the deposit detail itself.
                row_identity.pop("loan_number", None)
            loan_clients = load_detail_loan_clients([row_identity], client_catalog, allowed)
            detail_client, reason = choose_detail_client(
                row_identity, client_catalog, doc.employer, allowed, loan_clients,
            )
        has_identity = any(row_identity.get(field) for field in (
            "client_number", "national_id", "employee_number", "client_name", "loan_number",
        ))
        if not detail_client and has_identity:
            frappe.throw(_("No se pudo verificar de forma única al cliente de esta fila: {0}.").format(reason))
        if detail_row.get("client") and (not linked_client or linked_client.get("employer") not in allowed):
            frappe.throw(_("La identidad guardada en esta fila no coincide con los datos del cliente. Revise el cliente y sus identificadores."))
        detail_client = detail_client or linked_client
        if not detail_client:
            frappe.throw(_("No se pudo identificar de forma única al cliente de esta fila: {0}.").format(reason))
        frappe.get_doc("CN Client", detail_client["name"]).check_permission("read")
    periods = [frappe.get_doc("CN Reconciliation Period", row.name) for row in frappe.get_list(
        "CN Reconciliation Period", filters={"employer": ["in", allowed]},
        fields=["name"], order_by="payroll_month asc, name asc", limit_page_length=0,
    )]
    periods = [period for period in periods if period.has_permission("read")]
    open_periods = {p.name: p for p in periods if p.status != "Cerrado"}
    # Internal accounting totals must include allocations hidden by user permissions.
    deposits = frappe.get_all(
        "CN Remittance Allocation", filters={"docstatus": 1, "employer": ["in", reconciliation_companies(doc.employer)]},
        fields=["name", "docstatus", "allocation_detail"], limit_page_length=0,
    )
    items = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "category": ["not in", ["Saldo a favor de la empresa", "Saldo a favor del cliente", TOLERANCE_CATEGORY, "Ajuste de aplicación", "Compensación entre partidas"]]},
        fields=["name", "reference", "amount_usd", "employer", "period",
                "client_number", "loan_number", "installment_number", "description", "source_client_name",
                "credit_client", "voucher", "generic_distribution"],
        limit_page_length=0)
    linked_items = _allocate_complementary_items(items, periods)
    from credinomina_reconciliation.complementary_distribution import attach_company_scopes, company_scope
    attach_company_scopes(items)
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
        return {
            **{key: row.get(key) or "" for key in (
                "client_name", "client_number", "employee_number", "national_id", "loan_number",
            )},
            "client": row.get("client") or row.get("credit_client") or "",
        }

    from credinomina_reconciliation.period_totals import direct_amounts_by_collection
    direct_sources = list(_historical_sources(open_periods))
    direct_amounts = direct_amounts_by_collection([row for source in direct_sources for row in source.rows
        if row.historical_period in open_periods and row.effective and row.event_type == "Aplicacion" and row.match_status == "Conciliado"])
    direct_periods = {row.historical_period for source in direct_sources for row in source.rows if row.historical_period}
    candidates = []
    for period in open_periods.values():
        if period.reconciliation_mode == "Historica":
            continue
        for row in period.collection_rows:
            if not row.row_key or not requested({"period": period.name, "row_key": row.row_key}):
                continue
            candidates.append({**identity(row), "employer": period.employer, "kind": "Cobranza", "period": period.name,
                "period_label": period_label(period), "filter_period": period.name,
                "row_key": row.row_key, "reference": row.application_reference or "",
                "applied_usd": float(max(money(row.applied_usd) - direct_amounts[row.name], 0)),
                "due_usd": float(max(money(_deducted_amount(row, "USD")) - complementary[row.name] - direct_amounts[row.name]
                                     + min(money(row.rounding_adjustment_usd), money(0)), money(0)))})
            if period.name in direct_periods:
                candidates[-1]["due_usd"] = min(candidates[-1]["due_usd"], candidates[-1]["applied_usd"])
    for source in direct_sources:
        for row in source.rows:
            period = open_periods.get(row.historical_period)
            if (not period
                    or row.event_type != "Aplicacion" or not row.effective
                    or row.currency != "USD" or row.match_status != "Conciliado"
                    or not requested({"historical_application": row.name})):
                continue
            candidates.append({**identity(row), "employer": period.employer, "kind": "Aplicación histórica" if period.reconciliation_mode == "Historica" else "Aplicación del core",
                "historical_application": row.name, "filter_period": period.name,
                "period_label": period_label(period),
                "applied_usd": net_application_amount(row),
                "reference": " · ".join(str(v) for v in [row.reference, row.voucher, row.receipt] if v),
                "due_usd": float(money(net_application_amount(row)) + min(money(row.rounding_adjustment_usd), money(0)))})
    for item in items:
        if not requested({"complementary_item": item.name}):
            continue
        period = open_periods.get(item.period)
        if item.period and not period:
            continue
        scope = company_scope(item) if item.get("generic_distribution") else {item.employer or (period.employer if period else None)}
        if not scope & set(allowed):
            continue
        if not frappe.has_permission("CN Complementary Item", "read", doc=item.name):
            continue
        candidates.append({**identity(item), "employer": item.employer if item.employer in allowed else sorted(scope & set(allowed))[0], "kind": "Partida complementaria",
            "client_name": item.source_client_name or item.description,
            "_client_identity_name": item.source_client_name or "",
            "complementary_item": item.name,
            "generic_distribution": bool(item.get("generic_distribution")),
            "filter_period": item.period or "", "period_label": period_label(period) if period else "Sin período",
            "reference": " · ".join(str(v) for v in [item.reference, item.voucher] if v),
            "due_usd": float(money(item.amount_usd))})
    if detail_row and detail_client:
        from credinomina_reconciliation.paying_employers import choose_detail_client

        identity_fields = ("client_number", "national_id", "employee_number", "client_name")
        loan_only_candidates = [candidate for candidate in candidates
                                if not candidate.get("client") and candidate.get("loan_number")
                                and not any(candidate.get(field) for field in identity_fields)]
        candidate_loan_clients = {}
        if loan_only_candidates:
            from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
            candidate_loan_clients = load_detail_loan_clients(loan_only_candidates, client_catalog, allowed)
        scoped_candidates = []
        clients_by_name = {client["name"]: client for client in client_catalog}
        for candidate in candidates:
            company = candidate.get("employer") or ""
            if company != detail_client.get("employer"):
                continue
            candidate_identity = dict(candidate)
            if "_client_identity_name" in candidate_identity:
                candidate_identity["client_name"] = candidate_identity.pop("_client_identity_name")
            linked = clients_by_name.get(candidate.get("client"))
            if linked:
                if linked.get("employer") != company:
                    continue
                resolved = linked
            else:
                if any(candidate_identity.get(field) for field in identity_fields):
                    # Identifiers and registered aliases identify the person;
                    # the credit can be corrected independently.
                    candidate_identity.pop("loan_number", None)
                resolved, _reason = choose_detail_client(
                    candidate_identity, client_catalog, doc.employer, allowed, candidate_loan_clients,
                )
            if not resolved or resolved["name"] != detail_client["name"]:
                continue
            scoped_candidates.append({**candidate, "client": resolved["name"]})
        candidates = scoped_candidates
    for candidate in candidates:
        candidate.pop("_client_identity_name", None)
    from credinomina_reconciliation.client_credit import load_credits
    reserved_credit = sum((money(item.amount_usd) for item in load_credits([doc.name])), money(0))
    result = pending_selection(candidates, deposits, doc.name, money(doc.amount_usd) - reserved_credit, targets)
    result["reserved_cents"] += int(reserved_credit * 100)
    result["employer"] = doc.employer
    result["allowed_employers"] = allowed
    result["modified"] = str(doc.modified)
    if detail_row and detail_client:
        result["detail_row"] = detail_row.name
        result["detail_client"] = {
            "name": detail_client["name"],
            "client_name": detail_client.get("client_name") or detail_row.client_name,
            "client_number": detail_client.get("client_number") or detail_row.client_number,
            "employee_number": detail_client.get("employee_number") or detail_row.employee_number,
            "employer": detail_client.get("employer") or detail_row.employer,
        }
    return result
