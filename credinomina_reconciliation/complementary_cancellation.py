"""Cancel a complement without redistributing unrelated cash or company pools."""
import json

import frappe
from frappe import _

from credinomina_reconciliation.application_adjustments import CATEGORY as ADJUSTMENT
from credinomina_reconciliation.complementary_distribution import company_scope
from credinomina_reconciliation.deposit_reconciliation import entries, frozen_cash, lock_cash_pool
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money, money_float


def _engine():
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import
    return cn_accounting_import


def _json_names(value):
    parsed = frappe.parse_json(value or "[]")
    return {name for name in parsed if isinstance(name, str) and name} if isinstance(parsed, list) else set()


def _application_periods(names):
    engine = _engine()
    result = set()
    ordered = sorted(names)
    for offset in range(0, len(ordered), 500):
        for row in frappe.get_all("CN Source Row", filters={"name": ["in", ordered[offset:offset + 500]]},
            fields=["historical_period", "collection_period", "collection_row_id", "application_allocation_detail", "allocation_detail"],
            limit_page_length=0):
            result.update(engine._source_linked_periods(row))
    return result


def _deposit_periods(deposits):
    from credinomina_reconciliation.remittance_periods import selected_periods
    periods, applications, items = set(), set(), set()
    for deposit in deposits:
        periods.update(selected_periods(deposit))
        for target in deposit.targets:
            periods.add(target.period)
            if target.historical_application:
                applications.add(target.historical_application)
            if target.complementary_item:
                items.add(target.complementary_item)
        for entry in entries(deposit.allocation_detail):
            periods.add(entry.get("periodo"))
            if entry.get("aplicacion_id"):
                applications.add(entry["aplicacion_id"])
            if entry.get("partida"):
                items.add(entry["partida"])
    periods.update(_application_periods(applications))
    if items:
        periods.update(frappe.get_all("CN Complementary Item", filters={"name": ["in", sorted(items)]},
                                     pluck="period", limit_page_length=0))
    return periods - {None, ""}


def prepare_cancellation(item):
    """Capture dependencies while the item still connects shared company pools."""
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    engine = _engine()
    companies = {company for owner in company_scope(item) for company in reconciliation_companies(owner)}
    lock_cash_pool(companies)
    periods = {item.period} - {None, ""}
    applications = {item.related_application} - {None, ""} if item.category == ADJUSTMENT else set()
    periods.update(_json_names(item.get("adjustment_periods")) if applications else ())
    periods.update(_application_periods(applications))
    if applications:
        collections = _json_names(item.get("adjustment_collection_rows"))
        if collections:
            periods.update(frappe.get_all("CN Collection Row", filters={"name": ["in", sorted(collections)]},
                                         pluck="parent", limit_page_length=0))

    deposit_names = set(frappe.get_all("CN Remittance Target", filters={
        "complementary_item": item.name, "parenttype": "CN Remittance Allocation",
    }, pluck="parent", limit_page_length=0))
    # Automatic allocations may have no target child. Match the exact JSON ID,
    # not a reference/date/name substring that could identify another payment.
    for deposit in frappe.get_all("CN Remittance Allocation", filters={
        "docstatus": 1, "allocation_detail": ["like", "%" + item.name + "%"],
    }, fields=["name", "allocation_detail"], limit_page_length=0):
        if any(entry.get("partida") == item.name for entry in entries(deposit.allocation_detail)):
            deposit_names.add(deposit.name)
    if item.get("registered_deposit"):
        deposit_names.add(item.registered_deposit)
    deposits = [frappe.get_doc("CN Remittance Allocation", name) for name in sorted(deposit_names)
                if frappe.db.get_value("CN Remittance Allocation", name, "docstatus") == 1]
    periods.update(_deposit_periods(deposits))
    # A periodless, loan-specific complement can also alter an operative claim.
    if item.loan_number and not item.get("generic_distribution") and item.category not in {ADJUSTMENT, engine.COMPANY_CREDIT, engine.CLIENT_CREDIT}:
        candidates = ([frappe.get_doc("CN Reconciliation Period", item.period)] if item.period else
                      engine._load_open_periods(["in", sorted(companies)]) if companies else [])
        mapping = engine._allocate_complementary_items([item], candidates)
        row_ids = {key[0] for key in mapping}
        periods.update(period.name for period in candidates if any(row.name in row_ids for row in period.collection_rows))
    companies.update(deposit.employer for deposit in deposits if deposit.employer)
    if periods:
        companies.update(frappe.get_all("CN Reconciliation Period", filters={"name": ["in", sorted(periods)]},
                                       pluck="employer", limit_page_length=0))
    lock_cash_pool(companies)
    for deposit in deposits:
        deposit.check_permission("write")
    closed = frappe.get_all("CN Reconciliation Period", filters={
        "name": ["in", sorted(periods)], "status": "Cerrado",
    }, pluck="name", limit_page_length=0) if periods else []
    if closed:
        frappe.throw(_("Reabra los períodos {0} antes de cancelar esta partida.").format(", ".join(sorted(closed))))
    return {"companies": sorted(companies), "periods": sorted(periods),
            "deposits": [deposit.name for deposit in deposits], "applications": sorted(applications)}


def _load_imports(periods, deposits, applications, companies):
    """Load only parents with linked applications or affected deposit mirrors."""
    parents = set()
    queries = []
    if applications:
        queries.append({"name": ["in", sorted(applications)]})
    if deposits:
        queries.append({"remittance_allocation": ["in", sorted(deposits)], "event_type": "Deposito"})
    if periods:
        names = [period.name for period in periods]
        parents.update(frappe.get_all("CN Accounting Import", filters={"historical_period": ["in", names]},
                                     pluck="name", limit_page_length=0))
        for field in ("historical_period", "collection_period"):
            queries.append({field: ["in", names], "event_type": "Aplicacion"})
        collections = [row.name for period in periods for row in period.collection_rows]
        if collections:
            queries.append({"collection_row_id": ["in", collections], "event_type": "Aplicacion"})
        # Older split links may contain only collection_row_id, not period.
        identifiers = [*names, *collections]
        exact_periods, exact_collections = set(names), set(collections)
        for offset in range(0, len(identifiers), 100):
            for row in frappe.get_all("CN Source Row", filters={
                "event_type": "Aplicacion", "parenttype": "CN Accounting Import",
            }, or_filters=[["application_allocation_detail", "like", "%" + name + "%"]
                           for name in identifiers[offset:offset + 100]],
                fields=["parent", "application_allocation_detail"], limit_page_length=0):
                if any(link.get("period") in exact_periods or link.get("periodo") in exact_periods
                       or link.get("collection_row_id") in exact_collections for link in entries(row.application_allocation_detail)):
                    parents.add(row.parent)
    for filters in queries:
        parents.update(frappe.get_all("CN Source Row", filters={
            "parenttype": "CN Accounting Import", **filters,
        }, pluck="parent", limit_page_length=0))
    documents = [frappe.get_doc("CN Accounting Import", name) for name in sorted(parents)
                 if frappe.db.get_value("CN Accounting Import", name, "status") in {"Importado", "Importado con excepciones"}]
    engine = _engine()
    for company in sorted(companies):
        engine._company_imports(documents, company)
    if any(document.employer not in companies for document in documents):
        frappe.throw(_("Hay aplicaciones vinculadas de otra empresa; revise sus períodos antes de cancelar la partida."))
    return documents


def _related_cash(deposits, periods, rows, items):
    """Read-only evidence: other deposits sharing a claim, never all deposits."""
    selected = {deposit.name for deposit in deposits}
    names = {period.name for period in periods} | {row.name for row in rows if row.event_type == "Aplicacion"}
    names.update(item.name for item in items)
    found = {}
    fields = ["name", "deposit_reference", "deposit_voucher", "reconciliation_identity", "deposit_date",
              "deposit_currency", "deposit_amount", "amount_usd", "fx_rate", "employer", "allocation_detail",
              "allocated_usd", "unallocated_usd", "unclassified_usd", "justified_surplus_usd", "result"]
    # One SQL query per bounded chunk, instead of loading every company deposit.
    ordered = sorted(names)
    for offset in range(0, len(ordered), 100):
        for deposit in frappe.get_all("CN Remittance Allocation", filters={"docstatus": 1},
            or_filters=[["allocation_detail", "like", "%" + name + "%"] for name in ordered[offset:offset + 100]],
            fields=fields, limit_page_length=0):
            if deposit.name not in selected and any(
                entry.get("periodo") in names or entry.get("aplicacion_id") in names or entry.get("partida") in names
                for entry in entries(deposit.allocation_detail)
            ):
                found[deposit.name] = deposit
    return list(found.values())


def _scoped_items(items, periods, deposits, mapping):
    """Limit claim capacity evidence to the touched periods and cash targets."""
    period_names = {period.name for period in periods}
    collection_ids = {row.name for period in periods for row in period.collection_rows}
    referenced = {target.complementary_item for deposit in deposits for target in deposit.targets}
    referenced.update(entry.get("partida") for deposit in deposits for entry in entries(deposit.allocation_detail))
    references = {deposit.deposit_reference for deposit in deposits}
    referenced.update(item.name for (row_id, _reference), matches in mapping.items()
                      if row_id in collection_ids for item in matches)
    return [item for item in items if item.name in referenced or item.period in period_names
            or (not item.period and item.reference in references and not item.get("generic_distribution"))]


def reconcile_cancellation(item, scope):
    """One atomic pass: mutable affected deposits + frozen related bank cash."""
    if not any(scope[field] for field in ("deposits", "periods", "applications")):
        return {"companies": [], "deposits": [], "periods": [], "saved_imports": 0}
    engine = _engine()
    from credinomina_reconciliation.application_adjustments import refresh_rows, mark_mixed_settlements, assert_cash_preserved
    if not frappe.has_permission("CN Accounting Import", "write"):
        frappe.throw(_("No tiene permiso para conciliar importaciones."), frappe.PermissionError)
    lock_cash_pool(scope["companies"])
    deposits = [frappe.get_doc("CN Remittance Allocation", name) for name in scope["deposits"]]
    periods = [frappe.get_doc("CN Reconciliation Period", name) for name in scope["periods"]]
    for deposit in deposits:
        deposit.check_permission("write")
        if deposit.docstatus != 1:
            frappe.throw(_("El depósito {0} ya no está confirmado. Recargue la partida.").format(deposit.name))
        # Its target to the just-canceled item is intentionally invalid now;
        # the engine marks it for review rather than asking to confirm it again.
    if any(period.status == "Cerrado" for period in periods):
        frappe.throw(_("Reabra los períodos afectados antes de cancelar la partida."))
    # Application reductions do not change cash. Cancellation restores the net
    # application while retaining ALL existing deposit allocations verbatim.
    mutable = [] if item.category == ADJUSTMENT else deposits
    imports = _load_imports(periods, [deposit.name for deposit in deposits], scope["applications"], set(scope["companies"]))
    collection_ids = {row.name for period in periods for row in period.collection_rows}
    # A parent may contain rows from other periods. They remain untouched and
    # cannot accidentally become claims of an affected deposit.
    rows = [row for document in imports for row in document.rows if (
        row.name in scope["applications"] or (row.event_type == "Aplicacion" and (
            row.historical_period in scope["periods"] or any(link["collection_row_id"] in collection_ids
                                                           for link in engine._application_allocations(row))))
        or (row.event_type == "Deposito" and row.remittance_allocation in scope["deposits"]))]
    for document in [*imports, *periods]:
        document.check_permission("write")
        document._reconciliation_original_state = document_state(document)
    for document in imports:
        for row in document.rows:
            row._source_import, row._source_employer = document.name, document.employer
            row._historical_backfill = row.processing_route == "Historica" or (
                row.processing_route != "Operativa" and bool(document.historical_backfill or document.historical_period))
    items = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "employer": ["in", scope["companies"]],
        "category": ["not in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT, engine.TOLERANCE_CATEGORY, ADJUSTMENT, "Compensación entre partidas"]]},
        fields=["name", "reference", "amount_usd", "employer", "period", "client_number", "loan_number", "installment_number", "generic_distribution",
                "receivable_origin", "category", "subcategory_effect", "docstatus", "accounting_source_key"],
        limit_page_length=0)
    # Preserve global uniqueness for periodless loan complements, without
    # recalculating or saving the other periods used only as matching evidence.
    evidence_periods = engine._load_open_periods(["in", scope["companies"]]) if any(
        not complement.period and complement.loan_number and not complement.generic_distribution for complement in items
    ) else periods
    complementary = engine._allocate_complementary_items(items, evidence_periods)
    items = _scoped_items(items, periods, mutable, complementary)
    others = _related_cash(mutable, periods, rows, items)
    fixed_movements = []
    if others:
        for movement in frappe.get_all("CN Complementary Item", filters={"category": engine.TOLERANCE_CATEGORY,
            "docstatus": 1, "status": "Vigente", "deposit_source_row": ["in", [deposit.name for deposit in others]]},
            fields=["name", "period", "claim_id", "deposit_source_row", "application_source_row", "signed_amount_usd", "absorbed_cash_usd"],
            limit_page_length=0):
            fixed_movements.append({"name": movement.name, "period": movement.period, "claim_id": movement.claim_id,
                "deposit_id": movement.deposit_source_row, "application_id": movement.application_source_row,
                "signed_amount_usd": movement.signed_amount_usd, "consumed_residual_usd": movement.absorbed_cash_usd})
    # Frozen cash may include other payroll periods of the same deposit. Their
    # collection IDs are read-only mapping evidence, never part of write scope.
    extra = {entry.get("periodo") for deposit in others for entry in entries(deposit.allocation_detail)} - {period.name for period in periods}
    # A grouped application may cover two quincenas, although the canceled
    # complement affected only one. Read the other collection for status only.
    extra.update(period for row in rows if row.event_type == "Aplicacion" for period in engine._source_linked_periods(row))
    extra.difference_update(period.name for period in periods)
    evidence = [*periods, *(frappe.get_doc("CN Reconciliation Period", name) for name in sorted(extra - {None, ""}))]
    fixed_allocations, coverage = frozen_cash(others, evidence, fixed_movements)
    frozen_pairs, frozen_ids = engine._registered_deposit_pairs([], others)
    by_name = {deposit.name: deposit for deposit in others}
    frozen_meta = {}
    for account, bank in frozen_pairs:
        stored = by_name[account.name]
        for field in ("allocated_usd", "unallocated_usd", "unclassified_usd", "justified_surplus_usd", "allocation_detail"):
            account[field] = stored.get(field)
        frozen_meta[account.name] = {"account": account, "bank": bank, "employer": stored.employer,
            "amount_usd": stored.amount_usd, "nio_per_usd": stored.deposit_amount / stored.amount_usd
            if stored.deposit_currency == "NIO" and stored.amount_usd else 0}
    if scope["applications"]:
        changed = [row for row in rows if row.name in scope["applications"]]
        refresh_rows(changed)
        for row in changed:
            row.match_status, row.match_reason = "Pendiente", ""
            row.collection_period, row.collection_row_id, row.application_allocation_detail = "", "", "[]"
        engine._match_applications(changed, [row for period in periods for row in period.collection_rows],
            frozen_pairs, complementary, periods, fixed_sources=[row for row in rows if row.name not in scope["applications"]])
    pairs, registered = engine._registered_deposit_pairs(rows, mutable)
    allocation = engine._distribute_deposits(periods, rows, pairs, items, complementary, mutable, registered, fixed_coverage=coverage)
    for deposit in mutable:
        engine._sync_rounding_movements([movement for movement in allocation["rounding_movements"]
            if movement["deposit_id"] == deposit.name], allocation, rows, deposit_name=deposit.name)
    surplus = frappe.get_all("CN Complementary Item", filters={"docstatus": 1, "category": ["in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT]],
        "registered_deposit": ["in", [deposit.name for deposit in mutable]]},
        fields=["name", "period", "registered_deposit", "reference as deposit_reference", "deposit_voucher", "amount_usd", "result", "employer",
                "category", "credit_detail_row", "credit_client", "client_number", "client_name"],
        order_by="creation asc", limit_page_length=0) if mutable else []
    engine._classify_surplus(allocation, surplus)
    allocation["allocations"].extend(fixed_allocations)
    allocation["rounding_movements"].extend(fixed_movements)
    allocation["deposit_meta"].update(frozen_meta)
    allocation["balance_registered_ids"] = {**registered, **frozen_ids}
    active_ids = {row.name for period in periods for row in period.collection_rows}
    active_rows = [row for row in rows if row.event_type == "Aplicacion" and (row.name in scope["applications"]
        or row.historical_period in scope["periods"] or any(link["collection_row_id"] in active_ids for link in engine._application_allocations(row)))]
    for row in active_rows:
        detail = [{"movimiento": movement["name"], "diferencia_usd": movement["signed_amount_usd"], "periodo": movement["period"]}
                  for movement in allocation["rounding_movements"] if movement.get("application_id") == row.name]
        row.rounding_adjustment_usd = money_float(sum((money(entry["diferencia_usd"]) for entry in detail), money(0)))
        row.rounding_movement_detail = json.dumps(detail, ensure_ascii=False)
    engine._rebuild_period_balances([period for period in periods if period.reconciliation_mode != "Historica"],
        rows, pairs, allocation, {}, {}, items, source_status_collections=[row for period in evidence for row in period.collection_rows],
        status_source_ids={row.name for row in active_rows})
    engine._rebuild_historical_balances(periods, active_rows, allocation)
    mark_mixed_settlements(active_rows, [row for period in evidence for row in period.collection_rows])
    engine._sync_registered_deposit_detail(allocation)
    saved = 0
    for document in imports:
        document.recalculate_summary()
        document.status = "Importado con excepciones" if document.exception_count else "Importado"
        token = engine._source_reconcile_verified.set(True)
        try:
            saved += int(engine._save_reconciled_document(document))
        finally:
            engine._source_reconcile_verified.reset(token)
    assert_cash_preserved(item.flags.get("adjustment_cash_snapshot") or {})
    return {"companies": scope["companies"], "deposits": [deposit.name for deposit in mutable],
            "periods": scope["periods"], "saved_imports": saved}
