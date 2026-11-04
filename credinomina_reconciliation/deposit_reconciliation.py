"""Reconcile one confirmed deposit against capacity left by persisted deposits."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json

import frappe
from frappe import _
from frappe.utils import escape_html

from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.allocation_origin import MANUAL, UNRECORDED
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.remittance_periods import selected_periods
from credinomina_reconciliation.parsers import clean_text


def lock_cash_pool(companies):
    """Serialize cash mutations and reject an obsolete repeatable-read snapshot.

    Waiting for FOR UPDATE alone is insufficient: ordinary reads afterward can
    still see a snapshot established earlier in this transaction. Compare that
    snapshot's generation with the locked current generation before computing.
    A conflict rolls back the request; the next request starts with fresh data.
    """
    if companies:
        params = {"companies": tuple(sorted(companies))}
        query = "SELECT name, reconciliation_revision FROM `tabCN Employer` WHERE name IN %(companies)s ORDER BY name"
        before = frappe.db.sql(query, params, as_dict=True)
        current = frappe.db.sql(query + " FOR UPDATE", params, as_dict=True)
        revisions = lambda rows: {row["name"]: int(row.get("reconciliation_revision") or 0) for row in rows}
        if revisions(before) != revisions(current):
            frappe.throw(_("Otra operación actualizó los saldos de estas empresas mientras se procesaba la solicitud. Recargue y vuelva a conciliar; no se guardó una distribución desactualizada."))
        frappe.db.sql("UPDATE `tabCN Employer` SET reconciliation_revision=coalesce(reconciliation_revision,0)+1 WHERE name IN %(companies)s", params)


def entries(value):
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (ValueError, TypeError):
        parsed = []
    return [entry for entry in parsed if isinstance(entry, dict)] if isinstance(parsed, list) else []


def frozen_cash(deposits, periods, movements):
    """Actual cash and signed tolerance coverage; planned targets reserve no cash."""
    row_ids = {}
    for period in periods:
        for row in period.collection_rows:
            key = (period.name, clean_text(row.get('row_key')))
            if key in row_ids and row_ids[key] != row.name:
                frappe.throw(_('El período {0} tiene varias cuotas con la misma Fila ID ({1}). '
                    'Revise sus identificadores antes de conciliar; no se modificó la distribución guardada.').format(
                        period.name, key[1] or _('sin identificar')))
            row_ids[key] = row.name
    allocations, coverage = [], defaultdict(lambda: money(0))
    # Validate every stored deposit from one pass over its allocations.
    recorded_by_deposit = defaultdict(lambda: money(0))
    for deposit in deposits:
        for entry in stored_entries(deposit):
            kind = entry.get("tipo")
            from credinomina_reconciliation.company_conversion import KIND as FX_KIND, PREFIX as FX_PREFIX
            if kind == FX_KIND:
                proof = entry.get("conversion_empresa") or {}
                amount = money(entry.get("importe_usd"))
                if (proof.get("deposit") != deposit.name or money(proof.get("amount_usd")) != amount
                        or amount != money(-.01) or not proof.get("application_ids")):
                    frappe.throw(_("La diferencia de empresa guardada no tiene documentación válida. Revise el depósito."))
                allocations.append(dict(deposit_id=deposit.name, claim_id=FX_PREFIX + deposit.name,
                    amount_usd=money_float(amount), origin=FX_KIND, company_conversion=proof))
                recorded_by_deposit[deposit.name] += amount
                continue
            claim = (
                "H:" + entry.get("aplicacion_id", "") if kind == "Aplicacion historica"
                else "X:" + entry.get("partida", "") if kind == "Partida complementaria"
                else "C:" + row_ids.get((entry.get("periodo"), clean_text(entry.get("fila_id"))), "") if kind == "Cobranza"
                else ""
            )
            if kind == "Movimiento de conciliación":
                continue
            if not claim:
                frappe.throw(_("La distribución guardada del depósito {0} contiene un tipo de destino desconocido. Revísela antes de conciliar.").format(deposit.name))
            if not claim[2:]:
                frappe.throw(_("El depósito {0} tiene un destino que ya no existe. Revise su distribución antes de conciliar.").format(deposit.name))
            amount = money(entry.get("importe_usd"))
            if not amount or (amount < 0 and kind != "Partida complementaria"):
                frappe.throw(_("La distribución guardada del depósito {0} contiene un importe inválido. Revísela antes de conciliar.").format(deposit.name))
            allocations.append({"deposit_id": deposit.name, "claim_id": claim,
                                "amount_usd": money_float(amount), "origin": entry.get("origen") or UNRECORDED,
                                **({"detail_row": entry["fila_detalle"]} if entry.get("fila_detalle") else {}),
                                **({"fifo_applications": entry["aplicaciones_fifo"]} if entry.get("aplicaciones_fifo") else {}),
                                **({"group": entry["empresa"]} if entry.get("empresa") else {}),
                                **({"reclassification_operation_id": entry["reclassification_operation_id"]}
                                   if entry.get("reclassification_operation_id") else {})})
            coverage[claim] += amount
            recorded_by_deposit[deposit.name] += amount
    for movement in movements:
        coverage[movement["claim_id"]] += money(movement["consumed_residual_usd"]) - money(movement["signed_amount_usd"])
        recorded_by_deposit[movement["deposit_id"]] += money(movement["consumed_residual_usd"])
    for deposit in deposits:
        expected = deposit.get("allocated_usd")
        if expected is None:
            continue
        if recorded_by_deposit[deposit.name] != money(expected):
            frappe.throw(_("La distribución guardada del depósito {0} no coincide con su importe asignado. Revise ese depósito antes de continuar.").format(deposit.name))
    return allocations, coverage


def stored_entries(deposit):
    """Malformed persisted evidence must never be treated as an empty ledger."""
    raw = deposit.get("allocation_detail") or "[]"
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError()
        for row in rows:
            value = Decimal(str(row.get("importe_usd")))
            if not value.is_finite():
                raise ValueError()
    except (ValueError, TypeError, InvalidOperation):
        frappe.throw(_("La distribución guardada del depósito {0} no es válida. Revísela antes de conciliar.").format(deposit.name))
    return rows


def _legacy_manual_rows(document, allocations):
    """Resolve missing historical row IDs only from matching saved evidence."""
    amounts, targets, matches = defaultdict(lambda: money(0)), defaultdict(list), defaultdict(list)
    row_targets, rows, empty_cache = defaultdict(list), {}, set()
    for entry in allocations:
        if (entry["claim_id"].startswith(("H:", "X:")) and entry.get("origin") == MANUAL
                and not entry.get("detail_row") and not entry.get("reclassification_operation_id")):
            amounts[entry["claim_id"]] += money(entry["amount_usd"])
    if not amounts:
        return {}
    for target in document.get("targets") or []:
        if target.get("detail_row"):
            row_targets[target.detail_row].append(target)
        if target.get("historical_application") and not target.get("complementary_item"):
            targets["H:" + target.historical_application].append(target)
        elif target.get("complementary_item") and not target.get("historical_application"):
            targets["X:" + target.complementary_item].append(target)
    for row in document.get("detail_rows") or []:
        rows[row.name] = row
        raw = row.get("matched_targets")
        try:
            parsed = json.loads(raw or "[]") if isinstance(raw, str) else raw or []
            if parsed == []:
                empty_cache.add(row.name)
        except (TypeError, ValueError):
            pass  # Invalid or conflicting cache data is not proof of an empty cache.
        saved = entries(row.get("matched_targets"))
        for match in saved:
            if match.get("claim_id") in amounts:
                matches[match["claim_id"]].append((row, match, saved))
    result = {}
    for claim, amount in amounts.items():
        if len(targets[claim]) != 1:
            continue  # Shared or unidentified legacy cash requires explicit review.
        target = targets[claim][0]
        if not matches[claim] and claim.startswith("H:") and target.get("detail_row") in empty_cache:
            row = rows[target.detail_row]
            peers = row_targets[row.name]
            # Old applied targets are authoritative when the display cache was
            # cleared. Every target in the row must still match recorded cash;
            # a new, resized or pending target cannot supply this proof.
            if (peers and all(peer.get("name") and peer.get("result") == "Aplicada"
                    and peer.get("historical_application") and not peer.get("complementary_item")
                    and len(targets["H:" + peer.historical_application]) == 1
                    and money(peer.get("amount_usd")) > 0
                    and money(peer.get("amount_usd")) == amounts.get("H:" + peer.historical_application)
                    for peer in peers)
                    and sum((money(peer.amount_usd) for peer in peers), money(0)) == money(row.get("linked_usd"))):
                result[claim] = row.name
            continue
        if len(matches[claim]) != 1:
            continue
        row, match, saved = matches[claim][0]
        if (target.get("name") and target.get("detail_row") == row.name
                and match.get("instruction_id") == target.name
                and money(match.get("amount_usd")) == amount != 0
                and money(target.get("amount_usd")) * amount > 0
                and abs(money(target.get("amount_usd"))) >= abs(amount)
                and sum((money(part.get("amount_usd")) for part in saved), money(0)) == money(row.get("linked_usd"))):
            result[claim] = row.name
    return result


def _link_amounts(allocations, deposit_name, manual_rows=None):
    amounts = defaultdict(lambda: money(0))
    for entry in allocations:
        if entry["deposit_id"] != deposit_name:
            continue
        if entry.get("company_conversion"):
            continue  # Employer FX evidence is not a loan or imported-core cash link.
        detail_row = entry.get("detail_row") or ""
        if not detail_row and entry.get("origin") == MANUAL and not entry.get("reclassification_operation_id"):
            detail_row = (manual_rows or {}).get(entry["claim_id"], "")
        key = (entry["claim_id"], detail_row,
               entry.get("reclassification_operation_id") or "")
        parts = entry.get("fifo_applications") if entry["claim_id"].startswith("C:") else None
        if parts:
            if (not isinstance(parts, list) or any(not isinstance(part, dict) or not part.get("application_id")
                    or money(part.get("amount_usd")) <= 0 for part in parts)
                    or sum((money(part.get("amount_usd")) for part in parts), money(0)) != money(entry["amount_usd"])):
                frappe.throw(_("La distribución FIFO guardada no coincide con sus aplicaciones. Revise el depósito."))
            for part in parts:
                amounts[(*key, part["application_id"])] += money(part.get("amount_usd"))
        else:
            amounts[(*key, "")] += money(entry["amount_usd"])
    return amounts


def assert_preserved_allocations(document, allocation, periods):
    """A normal reconcile can add cash, never withdraw a confirmed link.

    The explicit undo actions retain their existing permission and closure
    guards. Throwing here aborts all intermediate distributor writes too.
    """
    previous, _coverage = frozen_cash([document], periods, [])
    manual_rows = _legacy_manual_rows(document, previous)
    old = _link_amounts(previous, document.name, manual_rows)
    new = _link_amounts(allocation["allocations"], document.name, manual_rows)
    for key, amount in old.items():
        assigned = new.get(key, money(0))
        if amount * assigned <= 0 or abs(assigned) < abs(amount):
            # A stale company difference blocks every detail row, including
            # automatic FIFO rows with no manual instruction to explain it.
            context = (allocation.get("client_credit_context") or {}).get("contexts", {}).get(document.name, {})
            difference_error = (context.get("difference") or {}).get("error")
            if difference_error:
                frappe.throw(_("No se pudo conciliar el depósito {0}. Se conservaron las asignaciones "
                    "confirmadas y no se guardó ningún cambio.").format(escape_html(document.name))
                    + "<br><br>" + escape_html(str(difference_error)))
            # Explain rejected row instructions before suggesting withdrawal of
            # already confirmed cash; a missing authorization is not an undo.
            results = allocation.get("instruction_results") or {}
            reasons = list(dict.fromkeys(str(results[target.name]) for target in document.get("targets") or []
                if key[1] and target.get("detail_row") == key[1]
                and results.get(target.get("name")) not in (None, "", "Aplicada", "Pendiente")))
            if reasons:
                frappe.throw(_("No se pudo conciliar el depósito {0} porque un destino manual fue rechazado. "
                    "Se conservaron las asignaciones confirmadas y no se guardó ningún cambio.").format(escape_html(document.name))
                    + "<br><br>" + "<br>".join(escape_html(reason) for reason in reasons[:3]))
            frappe.throw(_("La conciliación cambiaría una asignación ya confirmada del depósito {0}. "
                "Use Desconciliar en la fila o en el depósito antes de modificar sus vínculos. No se guardó ningún cambio.").format(document.name))


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


def stored_cash_context(deposits, periods, pairs=None):
    """Read persisted distributions without matching details or writing cash.

    Importing applications must not replay old FIFO decisions. Reuse the same
    frozen-cash validation as an individual deposit reconciliation, including
    signed complementary items and existing tolerance movements.
    """
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine

    if pairs is None:
        pairs, _ids = engine._registered_deposit_pairs([], deposits)
    stored = {deposit.name: deposit for deposit in deposits}
    meta = {}
    for account, bank in pairs:
        deposit = stored[account.name]
        for row in (account, bank):
            for field in ("allocated_usd", "unallocated_usd", "unclassified_usd", "justified_surplus_usd", "allocation_detail"):
                row[field] = deposit.get(field)
        meta[account.name] = {"account": account, "bank": bank, "employer": deposit.employer,
            "amount_usd": deposit.amount_usd,
            "nio_per_usd": deposit.deposit_amount / deposit.amount_usd
            if deposit.deposit_currency == "NIO" and deposit.amount_usd else 0}
    movements = []
    if stored:
        for item in frappe.get_all("CN Complementary Item", filters={
            "category": engine.TOLERANCE_CATEGORY, "docstatus": 1, "status": "Vigente",
            "deposit_source_row": ["in", list(stored)],
        }, fields=["name", "period", "claim_id", "deposit_source_row", "application_source_row",
                   "signed_amount_usd", "absorbed_cash_usd"], limit_page_length=0):
            movements.append({"name": item.name, "period": item.period, "claim_id": item.claim_id,
                "deposit_id": item.deposit_source_row, "application_id": item.application_source_row,
                "signed_amount_usd": item.signed_amount_usd, "consumed_residual_usd": item.absorbed_cash_usd})
    allocations, coverage = frozen_cash(deposits, periods, movements)
    return {"allocations": allocations, "coverage": coverage, "deposit_meta": meta,
            "rounding_movements": movements, "registered_ids": {name: name for name in stored}}


def reconcile_deposit(document, progress=None, undo=False):
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
    from credinomina_reconciliation.manual_complementary_policy import assert_no_legacy_tolerance
    assert_no_legacy_tolerance({"deposit_source_row": document.name})
    if undo:
        # Legacy cash can exceed its current bank equivalent. Withdrawal must
        # remain possible, while identity, closure and credit guards still run.
        document._validate_deposit(reversing=True)
        document._assert_can_reverse_distribution()
        if frappe.get_all("CN Complementary Item", filters={
            "registered_deposit": document.name, "docstatus": 1,
            "category": ["in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT]],
        }, limit_page_length=1):
            frappe.throw(_("Cancele los saldos a favor vinculados antes de desconciliar el depósito."))
    else:
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
        "origin_deposit": ["is", "not set"],
        "category": ["not in", [engine.COMPANY_CREDIT, engine.CLIENT_CREDIT, engine.TOLERANCE_CATEGORY, engine.APPLICATION_ADJUSTMENT, "Compensación entre partidas", "Vinculado a depósito"]],
    }, fields=["name", "reference", "amount_usd", "employer", "period", "client_number", "loan_number", "legacy_installment_number as installment_number", "generic_distribution", "receivable_origin", "category", "subcategory_effect", "docstatus", "accounting_source_key"], limit_page_length=0)
    complementary_by_target = engine._allocate_complementary_items(complements, periods)
    from credinomina_reconciliation.reclassification_distribution import load_cash_claims
    complements.extend(load_cash_claims([document.name]))
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
    closed_links = (engine._operative_links(periods, originals, [document, *others], complements, closed_state)
                    if closed_state else {})
    report(35, _("Conciliando el detalle y los destinos de este depósito…"))
    pairs, registered = engine._registered_deposit_pairs(source_rows, [document])
    allocation = engine._distribute_deposits(periods, source_rows, [] if undo else pairs, complements,
                                           complementary_by_target, [] if undo else [document],
                                           {} if undo else registered, fixed_coverage=coverage,
                                           fixed_allocations=fixed_allocations)
    if not undo:
        assert_preserved_allocations(document, allocation, periods)
    affected = affected_periods(document, allocation, periods, source_rows, complements)
    if undo:
        pairs = []
        registered = {}
    active_periods = [period for period in periods if period.name in affected]
    for period in active_periods:
        period.check_permission("write")
    # Stale tolerance movements of this deposit can be reversed, others stay intact.
    engine._sync_rounding_movements(allocation["rounding_movements"], allocation, source_rows,
                                    deposit_name=document.name)
    from credinomina_reconciliation.client_credit import load_credits
    surplus = load_credits([document.name])
    for credit in surplus:
        credit.deposit_reference = credit.get("reference") or ""
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
    movements_by_application = defaultdict(list)
    for movement in allocation["rounding_movements"]:
        if movement.get("application_id"):
            movements_by_application[movement["application_id"]].append(movement)
    for row in active_sources:
        rounding = [{"movimiento": movement["name"], "diferencia_usd": movement["signed_amount_usd"],
                     "periodo": movement["period"]} for movement in movements_by_application.get(row.name, ())]
        row.rounding_adjustment_usd = money_float(sum((money(item["diferencia_usd"]) for item in rounding), money(0)))
        row.rounding_movement_detail = json.dumps(rounding, ensure_ascii=False)
    report(75, _("Actualizando los saldos de los períodos afectados…"))
    active_closed_state = {name: state for name, state in closed_state.items() if name in affected}
    active_closed_links = {name: links for name, links in closed_links.items() if name in affected}
    engine._rebuild_historical_balances(active_periods, active_sources, allocation)
    engine._rebuild_period_balances([period for period in active_periods if period.reconciliation_mode != "Historica"],
        source_rows, pairs, allocation, active_closed_state, active_closed_links, complements,
        source_status_collections=collections, status_source_ids={row.name for row in active_sources})
    mark_mixed_settlements(active_sources, collections)
    for row in active_sources:
        if row.effective and row.application_adjustment_usd and engine.net_amount(row) == 0:
            row.match_status = "Conciliado"
            row.deposit_match_status = "Aplicación compensada"
            row.deposit_match_reason = _("Aplicación compensada totalmente por ajustes confirmados; no es un depósito recibido.")
    # Sync can only write the selected registered deposit.
    engine._sync_registered_deposit_detail(allocation)
    if undo:
        from credinomina_reconciliation.reconciliation_audit import snapshot, record_transition
        before = snapshot(document)
        values = dict(allocated_usd=0, unallocated_usd=document.amount_usd,
                      unclassified_usd=document.amount_usd, justified_surplus_usd=0,
                      credit_coverage_usd=0, complementary_coverage_usd=0,
                      conversion_coverage_usd=0,
                      allocation_detail="[]", result="Pendiente",
                      detail_status="Detalle pendiente", inherited_exception_comment="")
        frappe.db.set_value(document.doctype, document.name, values)
        for row in document.detail_rows:
            frappe.db.set_value("CN Remittance Detail", row.name, {
                "match_status": "Pendiente", "match_reason": _("Depósito desconciliado."),
                "matched_targets": "[]", "matched_targets_summary": "",
                "linked_usd": 0, "client_credit_usd": 0, "company_credit_usd": 0, "pending_usd": row.amount_usd,
                **({"difference_core_pending_usd": abs(row.difference_amount_usd),
                    "difference_core_status": "Vinculada; pendiente de conciliar" if row.difference_core_item else "Pendiente de respaldo del core"}
                   if row.get("row_kind") == "Diferencia documentada" else {}),
            }, update_modified=False)
        record_transition(document.name, before, snapshot({**before, **values}))
        from credinomina_reconciliation.complementary_state import deposit_changed
        deposit_changed({"name": document.name, "allocation_detail": "[]"}, previous=before)
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
    if not undo:
        from credinomina_reconciliation.immaterial_differences import classify_document
        classify_document(document)
        for period in active_periods:
            classify_document(period)
        active_ids = {row.name for row in active_sources}
        for imported in imports:
            if imported.name in active_parents:
                classify_document(imported, row_names=active_ids)
    matched = sum(row.match_status in {"Conciliada", "No deducido", "Conciliada con diferencia documentada", "Diferencia documentada"}
                  for row in document.detail_rows)
    report(100, _("Conciliación del depósito finalizada."))
    return {"deposit": document.name, "result": document.result, "periods": sorted(affected),
            "detail_rows": len(document.detail_rows), "detail_matched": matched,
            "detail_pending": len(document.detail_rows) - matched, "targets": len(document.targets),
            "saved_imports": saved, "scope": "deposit"}
