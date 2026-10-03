"""Reconcile one confirmed deposit against capacity left by persisted deposits."""
from collections import defaultdict
import json

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.remittance_periods import selected_periods


def lock_cash_pool(companies):
    """Serialize company and individual-deposit runs sharing the same cash pool."""
    if companies:
        frappe.db.sql("SELECT name FROM `tabCN Employer` WHERE name IN %(companies)s ORDER BY name FOR UPDATE",
                      {"companies": tuple(sorted(companies))})


def entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (ValueError, TypeError):
        parsed = []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def frozen_cash(deposits, periods, movements):
    """Actual cash and signed tolerance coverage; planned targets reserve no cash."""
    row_ids = {(period.name, row.row_key): row.name for period in periods for row in period.collection_rows}
    allocations, coverage = [], defaultdict(lambda: money(0))
    for deposit in deposits:
        for entry in entries(deposit.allocation_detail):
            kind = entry.get("tipo")
            claim = (
                "H:" + entry.get("aplicacion_id", "") if kind == "Aplicacion historica"
                else "X:" + entry.get("partida", "") if kind == "Partida complementaria"
                else "C:" + row_ids.get((entry.get("periodo"), entry.get("fila_id")), "") if kind == "Cobranza"
                else ""
            )
            if not claim:
                continue
            if not claim[2:]:
                frappe.throw(_("El depósito {0} tiene un destino que ya no existe. Revise su distribución antes de conciliar.").format(deposit.name))
            amount = money(entry.get("importe_usd"))
            allocations.append({"deposit_id": deposit.name, "claim_id": claim,
                                "amount_usd": money_float(amount), "origin": entry.get("origen") or "Manual"})
            coverage[claim] += amount
    for movement in movements:
        coverage[movement["claim_id"]] += money(movement["consumed_residual_usd"]) - money(movement["signed_amount_usd"])
    for deposit in deposits:
        expected = deposit.get("allocated_usd")
        if expected is None:
            continue
        recorded = sum((money(entry["amount_usd"]) for entry in allocations if entry["deposit_id"] == deposit.name), money(0))
        recorded += sum((money(movement["consumed_residual_usd"]) for movement in movements
                         if movement["deposit_id"] == deposit.name), money(0))
        if recorded != money(expected):
            frappe.throw(_("La distribución guardada del depósito {0} no coincide con su importe asignado. Revise ese depósito antes de continuar.").format(deposit.name))
    return allocations, coverage


def affected_periods(deposit, allocation, periods, source_rows, complementary_items):
    collections = {"C:" + row.name: period.name for period in periods for row in period.collection_rows}
    applications = {"H:" + row.name: row.historical_period for row in source_rows if row.event_type == "Aplicacion"}
    complements = {"X:" + item.name: item.period for item in complementary_items}
    names = {entry.get("periodo") for entry in entries(deposit.allocation_detail)}
    names.update(movement.get("period") for movement in allocation["rounding_movements"])
    for entry in allocation["allocations"]:
        names.add(collections.get(entry["claim_id"]) or applications.get(entry["claim_id"]) or complements.get(entry["claim_id"]))
    for target in deposit.targets:
        names.add(target.period or applications.get("H:" + (target.historical_application or ""))
                  or complements.get("X:" + (target.complementary_item or "")))
    # An explicitly associated detail period may have no successfully matched row.
    names.update(selected_periods(deposit))
    return {name for name in names if name}


def reconcile_deposit(document, progress=None):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    from credinomina_reconciliation.application_adjustments import mark_mixed_settlements

    if not document.employer:
        frappe.throw(_("Indique la empresa del depósito antes de conciliar."))
    if not frappe.has_permission("CN Accounting Import", "write"):
        frappe.throw(_("No tiene permiso para conciliar importaciones."), frappe.PermissionError)
    report = progress or (lambda percent, message: None)
    report(5, _("Cargando los destinos disponibles para este depósito…"))
    companies = reconciliation_companies(document.employer)
    lock_cash_pool(companies)
    # Refresh after acquiring the shared lock; reject unsaved/stale request data.
    document.reload()
    document.check_permission("write")
    if document.docstatus != 1:
        frappe.throw(_("Confirme el depósito antes de conciliarlo."))
    document._validate_deposit()
    imports = engine.load_scoped_imports(companies)
    source_rows = [row for imported in imports for row in imported.rows]
    for imported in imports:
        imported._reconciliation_original_state = document_state(imported)
        for row in imported.rows:
            row._source_import, row._source_employer = imported.name, imported.employer
    scope = ["in", companies]
    periods = engine._load_open_periods(scope)
    for period in periods:
        period._reconciliation_original_state = document_state(period)
    collections = [row for period in periods for row in period.collection_rows]
    originals = [row.as_dict() for row in source_rows]
    complements = frappe.get_all("CN Complementary Item", filters={
        "docstatus": 1, "employer": scope,
        "category": ["not in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT, engine.TOLERANCE_CATEGORY, engine.APPLICATION_ADJUSTMENT, "Compensación entre partidas"]],
    }, fields=["name", "reference", "amount_usd", "employer", "period", "client_number", "loan_number", "installment_number", "generic_distribution"], limit_page_length=0)
    complementary_by_target = engine._allocate_complementary_items(complements, periods)
    others = frappe.get_all("CN Remittance Allocation", filters={
        "docstatus": 1, "employer": scope, "name": ["!=", document.name],
    }, fields=["name", "deposit_reference", "deposit_voucher", "reconciliation_identity", "deposit_date",
               "deposit_currency", "deposit_amount", "amount_usd", "fx_rate", "employer", "allocation_detail",
               "allocated_usd", "unallocated_usd", "unclassified_usd", "justified_surplus_usd", "result"], limit_page_length=0)
    # Reconstruct other deposits solely as read-only balance evidence.
    frozen_pairs, frozen_ids = engine._registered_deposit_pairs([], others)
    outside = {deposit.name: deposit for deposit in others}
    frozen_meta = {}
    for account, bank in frozen_pairs:
        stored = outside[account.name]
        for field in ("allocated_usd", "unallocated_usd", "unclassified_usd", "justified_surplus_usd", "allocation_detail"):
            account[field] = stored.get(field)
        frozen_meta[account.name] = {"account": account, "bank": bank, "employer": stored.employer,
                                     "amount_usd": stored.amount_usd,
                                     "nio_per_usd": stored.deposit_amount / stored.amount_usd
                                     if stored.deposit_currency == "NIO" and stored.amount_usd else 0}
    fixed_movements = []
    if outside:
        for item in frappe.get_all("CN Complementary Item", filters={
            "category": engine.TOLERANCE_CATEGORY, "docstatus": 1, "status": "Vigente",
            "deposit_source_row": ["in", list(outside)],
        }, fields=["name", "period", "claim_id", "deposit_source_row", "application_source_row",
                   "signed_amount_usd", "absorbed_cash_usd"], limit_page_length=0):
            fixed_movements.append({"name": item.name, "period": item.period, "claim_id": item.claim_id,
                                   "deposit_id": item.deposit_source_row, "application_id": item.application_source_row,
                                   "signed_amount_usd": item.signed_amount_usd,
                                   "consumed_residual_usd": item.absorbed_cash_usd})
    fixed_allocations, coverage = frozen_cash(others, periods, fixed_movements)
    closed_state = {period.name: engine._operative_period_state(period) for period in periods
                    if period.reconciliation_mode != "Historica" and period.status == "Cerrado"}
    closed_links = engine._operative_links(periods, originals, [document, *others], complements, closed_state)
    report(35, _("Conciliando el detalle y los destinos de este depósito…"))
    pairs, registered = engine._registered_deposit_pairs(source_rows, [document])
    allocation = engine._distribute_deposits(periods, source_rows, pairs, complements,
                                           complementary_by_target, [document], registered, fixed_coverage=coverage)
    affected = affected_periods(document, allocation, periods, source_rows, complements)
    active_periods = [period for period in periods if period.name in affected]
    for period in active_periods:
        period.check_permission("write")
    # Stale tolerance movements of this deposit can be reversed, others stay intact.
    engine._sync_rounding_movements(allocation["rounding_movements"], allocation, source_rows,
                                    deposit_name=document.name)
    surplus = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "category": ["in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT]],
                             "registered_deposit": document.name},
                             fields=["name", "period", "registered_deposit", "reference as deposit_reference",
                                     "deposit_voucher", "amount_usd", "result", "employer", "category", "credit_detail_row", "credit_client", "client_number", "client_name"], limit_page_length=0)
    engine._classify_surplus(allocation, surplus)
    # All existing cash is included in affected balances, but only this deposit
    # has been reallocated and only its detail/targets can be synchronized.
    allocation["allocations"].extend(fixed_allocations)
    allocation["rounding_movements"].extend(fixed_movements)
    allocation["deposit_meta"].update(frozen_meta)
    allocation["balance_registered_ids"] = {**registered, **frozen_ids}
    active_collection_ids = {row.name for period in active_periods for row in period.collection_rows}
    active_sources = [row for row in source_rows if row.event_type == "Aplicacion" and (
        row.historical_period in affected or any(link["collection_row_id"] in active_collection_ids
                                                for link in engine._application_allocations(row)))]
    for row in active_sources:
        rounding = [{"movimiento": movement["name"], "diferencia_usd": movement["signed_amount_usd"],
                     "periodo": movement["period"]} for movement in allocation["rounding_movements"]
                    if movement.get("application_id") == row.name]
        row.rounding_adjustment_usd = money_float(sum((money(item["diferencia_usd"]) for item in rounding), money(0)))
        row.rounding_movement_detail = json.dumps(rounding, ensure_ascii=False)
    report(75, _("Actualizando los saldos de los períodos afectados…"))
    active_closed_state = {name: state for name, state in closed_state.items() if name in affected}
    active_closed_links = {name: links for name, links in closed_links.items() if name in affected}
    engine._rebuild_period_balances([period for period in active_periods if period.reconciliation_mode != "Historica"],
        source_rows, pairs, allocation, active_closed_state, active_closed_links, complements,
        source_status_collections=collections, status_source_ids={row.name for row in active_sources})
    engine._rebuild_historical_balances(active_periods, active_sources, allocation)
    mark_mixed_settlements(active_sources, collections)
    for row in active_sources:
        if row.effective and row.application_adjustment_usd and engine.net_amount(row) == 0:
            row.match_status = "Conciliado"
            row.deposit_match_status = "Aplicación compensada"
            row.deposit_match_reason = _("Aplicación compensada totalmente por ajustes confirmados; no es un depósito recibido.")
    # Sync can only write the selected registered deposit.
    engine._sync_registered_deposit_detail(allocation)
    active_parents = {row.parent for row in active_sources}
    for account, bank in pairs:
        if bank.name != document.name and bank.get("parent"):
            active_parents.add(bank.parent)
    saved = 0
    for imported in imports:
        if imported.name not in active_parents:
            continue
        imported.recalculate_summary()
        imported.status = "Importado con excepciones" if imported.exception_count else "Importado"
        token = engine._source_reconcile_verified.set(True)
        try:
            saved += int(engine._save_reconciled_document(imported))
        finally:
            engine._source_reconcile_verified.reset(token)
    document.reload()
    matched = sum(row.match_status in {"Conciliada", "No deducido"} for row in document.detail_rows)
    report(100, _("Conciliación del depósito finalizada."))
    return {"deposit": document.name, "result": document.result, "periods": sorted(affected),
            "detail_rows": len(document.detail_rows), "detail_matched": matched,
            "detail_pending": len(document.detail_rows) - matched, "targets": len(document.targets),
            "saved_imports": saved, "scope": "deposit"}
