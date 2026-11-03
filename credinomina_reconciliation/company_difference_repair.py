"""Repair the old one-cent client haircut using only the recorded allocation."""
import copy
import json

import frappe
from frappe.utils import escape_html

from credinomina_reconciliation.company_conversion import KIND, journal_entry
from credinomina_reconciliation.deposit_difference import KIND as DOCUMENTED, basis
from credinomina_reconciliation.rounding import CENT_PRECISION, money, money_float, sum_money


class ReviewRequired(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReviewRequired(message)


def entries(raw):
    try:
        result = json.loads(raw or "[]") if isinstance(raw, str) else copy.deepcopy(raw or [])
    except (TypeError, ValueError):
        raise ReviewRequired("El diario guardado no es válido.")
    require(isinstance(result, list) and all(isinstance(row, dict) for row in result), "El diario guardado no es válido.")
    return result


def plan(deposit, sources):
    """Add coverage to the exact saved destination; never select new targets."""
    ledger = entries(deposit.get("allocation_detail"))
    if any(row.get("tipo") == KIND for row in ledger):
        return None
    require(deposit.docstatus == 1 and deposit.get("apply_fifo") and not deposit.get("targets"),
            "El depósito no corresponde a una distribución FIFO automática confirmada.")
    require(money(deposit.allocated_usd) == money(deposit.amount_usd)
            and not any(money(deposit.get(field)) for field in
                ("unallocated_usd", "unclassified_usd", "justified_surplus_usd")),
            "El depósito no tiene todo su efectivo distribuido o conserva saldos documentados.")
    differences = [row for row in deposit.detail_rows if row.get("row_kind") == DOCUMENTED]
    require(len(differences) == 1, "Debe existir una única diferencia documentada de empresa.")
    difference = differences[0]
    require(money(difference.difference_amount_usd) == -CENT_PRECISION
            and not difference.get("difference_core_item") and difference.get("difference_reason")
            and difference.get("difference_basis") == basis(deposit, deposit.detail_rows),
            "La documentación de empresa cambió o ya tiene respaldo del core.")
    ordinary = [row for row in deposit.detail_rows if row.get("row_kind") != DOCUMENTED and money(row.get("amount_usd"))]
    require(ordinary and all(money(row.amount_usd) > 0 for row in ordinary), "Hay importes no identificables en el detalle.")
    require(sum_money(row.amount_usd for row in ordinary) == money(deposit.amount_usd) + CENT_PRECISION,
            "La diferencia del detalle no es exactamente un centavo.")
    require(sum_money(row.pending_usd for row in ordinary) == CENT_PRECISION
            and len([row for row in ordinary if money(row.pending_usd)]) == 1,
            "El centavo no corresponde a una única fila pendiente.")
    require(len(ledger) == len(ordinary) and sum_money(row.get("importe_usd") for row in ledger) == money(deposit.allocated_usd),
            "El detalle no coincide con la distribución guardada.")
    row_updates, source_updates, periods, seen = {}, {}, set(), set()
    gap_source = None
    for detail in ordinary:
        require(not any(money(detail.get(field)) for field in ("client_credit_usd", "company_credit_usd")),
                "Hay saldos a favor vinculados a una fila.")
        portions = [entry for entry in ledger if entry.get("fila_detalle") == detail.name]
        require(len(portions) == 1, "Una fila tiene destinos compartidos o sin trazabilidad exacta.")
        entry = portions[0]
        source_id = entry.get("aplicacion_id")
        require(entry.get("tipo") == "Aplicacion historica" and source_id in sources and source_id not in seen,
                "Una aplicación tiene múltiples destinos o no está disponible.")
        seen.add(source_id)
        source = sources[source_id]
        paid, pending = money(entry.get("importe_usd")), money(detail.pending_usd)
        require(pending in (money(0), CENT_PRECISION) and paid > 0
                and paid == money(detail.linked_usd) == money(detail.amount_usd) - pending,
                "La cobertura guardada del cliente no coincide con su pendiente.")
        require(source.get("event_type") == "Aplicacion" and source.get("effective")
                and source.get("match_status") == "Conciliado" and source.get("historical_period") == entry.get("periodo")
                and money(source.get("net_applied_usd")) == money(detail.amount_usd)
                and money(source.get("historical_remitted_usd")) == paid
                and money(source.get("historical_balance_usd")) == pending
                and not money(source.get("application_adjustment_usd"))
                and not money(source.get("rounding_adjustment_usd")),
                "La aplicación tiene otra cobertura, un ajuste o un saldo que no corresponde al centavo.")
        periods.add(source.historical_period)
        history = entries(source.get("historical_detail"))
        require(len(history) == 1 and money(history[0].get("importe_usd")) == paid
                and history[0].get("referencia") == deposit.deposit_reference
                and str(history[0].get("comprobante") or "") == str(deposit.get("deposit_voucher") or "")
                and str(history[0].get("fecha") or "")[:10] == str(deposit.deposit_date)[:10]
                and not history[0].get("saldo_a_favor") and not history[0].get("conversion_empresa")
                and not money(history[0].get("diferencia_usd")),
                "El historial incluye otra conciliación o una distribución que no puede atribuirse con certeza.")
        targets = entries(detail.get("matched_targets"))
        require(len(targets) == 1 and targets[0].get("claim_id") == "H:" + source_id
                and money(targets[0].get("amount_usd")) == paid,
                "Los destinos de la fila no coinciden con el diario del depósito.")
        for parent, field in ((entry, "aplicaciones_fifo"), (targets[0], "fifo_applications")):
            parts = parent.get(field)
            require(isinstance(parts, list) and len(parts) == 1
                    and parts[0].get("application_id") == source_id
                    and money(parts[0].get("amount_usd")) == paid,
                    "El desglose FIFO no identifica exactamente la aplicación guardada.")
            if pending:
                parts[0]["amount_usd"] = money_float(paid + pending)
        if pending:
            gap_source = source_id
            entry["importe_usd"] = targets[0]["amount_usd"] = money_float(paid + pending)
            history[0]["importe_usd"] = money_float(paid + pending)
            from credinomina_reconciliation.remittance_target_summary import describe_targets
            description = dict(source.as_dict(), period=source.historical_period)
            row_updates[detail.name] = dict(linked_usd=money_float(paid + pending), pending_usd=0,
                matched_targets=json.dumps(targets, ensure_ascii=False),
                matched_targets_summary=describe_targets(targets, {"H:" + source_id: description}),
                match_status="Conciliada", match_reason="Importe del cliente conservado íntegramente; el centavo permanece documentado en la empresa.")
        source_updates[source_id] = dict(historical_detail=history)
    require(len(periods) == 1 and None not in periods and "" not in periods, "La diferencia está compartida entre varios períodos.")
    proof = dict(deposit=deposit.name, employer=deposit.employer, period=next(iter(periods)),
        difference_row=difference.name, amount_usd=-.01, difference_reason=difference.difference_reason,
        detail_basis=difference.difference_basis, application_ids=sorted(seen))
    ledger.append(journal_entry(proof))
    require(sum_money(entry["importe_usd"] for entry in ledger) == money(deposit.allocated_usd),
            "La reparación no conserva el efectivo registrado.")
    for source_id, values in source_updates.items():
        for entry in values["historical_detail"]:
            entry["conversion_empresa"] = proof
        values["historical_detail"] = json.dumps(values["historical_detail"], ensure_ascii=False)
        if source_id == gap_source:
            values.update(historical_remitted_usd=money_float(money(sources[source_id].historical_remitted_usd) + CENT_PRECISION),
                historical_balance_usd=0, deposit_match_status="Depósito conciliado",
                deposit_match_reason="Cobertura completa del cliente; diferencia inmaterial documentada en la empresa.")
    return dict(ledger=ledger, sources=source_updates, details=row_updates, proof=proof, gap_source=gap_source)


def _equal(left, right):
    return left is None and right is None or left is not None and right is not None and money(left) == money(right)


def repair(deposit_name):
    """No save hooks, reconciliation calls, commits or mutations of other deposits."""
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    from credinomina_reconciliation.period_totals import PeriodTotalsContext
    from credinomina_reconciliation.accounting_client_summary import stored_balance_totals
    from credinomina_reconciliation.reconciliation_audit import snapshot, record_transition, audit_reason
    from credinomina_reconciliation.difference_documentation import _touch_documentation

    employer = frappe.db.get_value("CN Remittance Allocation", deposit_name, "employer")
    lock_cash_pool(reconciliation_companies(employer))
    deposit = frappe.get_doc("CN Remittance Allocation", deposit_name, for_update=True)
    ledger = entries(deposit.get("allocation_detail"))
    if any(entry.get("tipo") == KIND for entry in ledger):
        return False
    source_ids = sorted({entry.get("aplicacion_id") for entry in ledger} - {None, ""})
    require(source_ids, "El depósito no tiene aplicaciones guardadas para corregir.")
    parents = {row.parent for row in frappe.get_all("CN Source Row", filters={
        "name": ["in", source_ids], "parenttype": "CN Accounting Import", "parentfield": "rows"}, fields=["parent"], limit_page_length=0)}
    imports = [frappe.get_doc("CN Accounting Import", name, for_update=True) for name in sorted(parents)]
    require(all(doc.employer == employer and doc.docstatus != 2 and doc.status in {"Importado", "Importado con excepciones"}
                for doc in imports), "Las aplicaciones no pertenecen a importaciones vigentes de la misma empresa.")
    sources = {row.name: row for doc in imports for row in doc.rows if row.name in source_ids}
    proposal = plan(deposit, sources)
    require(proposal is not None, "La diferencia ya está corregida.")
    period = frappe.get_doc("CN Reconciliation Period", proposal["proof"]["period"], for_update=True)
    require(period.employer == employer, "El período corresponde a otra empresa.")
    # Direct applications projected onto payroll need their exact projection
    # updated too. Ambiguous/shared projections are left for explicit review.
    gap = sources[proposal["gap_source"]]
    require(not gap.get("collection_row_id") and gap.get("application_allocation_detail") in (None, "", "[]"),
            "La aplicación tiene proyecciones de cobranza que requieren revisión explícita.")
    before_period = PeriodTotalsContext([period]).values(period)
    period_fields = ("applied_total_usd", "deposit_coverage_usd", "complementary_coverage_usd",
                     "remitted_total_usd", "pending_usd", "conversion_coverage_usd", "negative_complementary_usd")
    require(all(_equal(period.get(field), before_period.get(field)) for field in period_fields),
            "Los totales del período requieren otra revisión; no se recalculará su conciliación.")
    before_imports = {doc.name: stored_balance_totals(doc) for doc in imports}
    # A stale parent summary is unrelated work; do not silently repair it while
    # updating the one client's cent.
    for doc in imports:
        summary = (doc.matched_count, doc.exception_count, doc.status)
        doc.recalculate_reconciliation_summary()
        require(summary == (doc.matched_count, doc.exception_count, doc.status),
                "El resumen de la importación requiere revisión antes de corregir el centavo.")
    audit_before = snapshot(deposit)
    for source_id, values in proposal["sources"].items():
        frappe.db.set_value("CN Source Row", source_id, values, update_modified=False)
        sources[source_id].update(values)
    for name, values in proposal["details"].items():
        frappe.db.set_value("CN Remittance Detail", name, values, update_modified=False)
    deposit_values = dict(allocation_detail=json.dumps(proposal["ledger"], ensure_ascii=False),
        result="Conciliado con diferencia documentada", financial_status="Conciliado con diferencia documentada",
        detail_status="Conciliado con diferencia documentada", conversion_coverage_usd=.01)
    frappe.db.set_value(deposit.doctype, deposit.name, deposit_values, update_modified=False)
    after_period = PeriodTotalsContext([period]).values(period)
    deltas = dict(remitted_total_usd=CENT_PRECISION, pending_usd=-CENT_PRECISION, conversion_coverage_usd=CENT_PRECISION)
    require(all(after_period.get(field) is not None and before_period.get(field) is not None
                and money(after_period[field]) - money(before_period[field]) == deltas.get(field, 0)
                for field in period_fields), "La reparación alteraría otros saldos del período.")
    period_values = {field: after_period[field] for field in period_fields if field in deltas}
    if period.reconciliation_mode == "Historica":
        period_values["remitted_usd"] = money_float(money(period.remitted_usd) + CENT_PRECISION)
        period_values["exception_count"] = max(int(period.exception_count or 0) - 1, 0)
        if (period.status != "Cerrado" and not money(after_period["pending_usd"])
                and not period_values["exception_count"] and not money(period.get("unassigned_deposit_usd"))):
            period_values["status"] = "Conciliado"
    frappe.db.set_value(period.doctype, period.name, period_values, update_modified=False)
    for doc in imports:
        after = stored_balance_totals(doc)
        before = before_imports[doc.name]
        expected = -CENT_PRECISION if gap.parent == doc.name else 0
        require(all(_equal(before[field], after[field]) for field in ("amount_usd", "deposits_usd", "complementary_usd"))
                and (before["balance_usd"] is None and after["balance_usd"] is None
                     or before["balance_usd"] is not None and after["balance_usd"] is not None
                     and money(after["balance_usd"]) - money(before["balance_usd"]) == expected),
                "La reparación alteraría otros saldos de la importación.")
        doc.recalculate_reconciliation_summary()
        frappe.db.set_value(doc.doctype, doc.name, dict(matched_count=doc.matched_count,
            exception_count=doc.exception_count, status=doc.status, balance_usd=after["balance_usd"]), update_modified=False)
    deposit.update(deposit_values)
    with audit_reason("Parche: diferencia inmaterial de empresa", "Centavo trasladado del cliente a la empresa; efectivo y destinos originales conservados."):
        record_transition(deposit.name, audit_before, snapshot(deposit))
    for document in [deposit, period, *imports]:
        document.add_comment("Comment", "Parche de diferencia de empresa: US$ 0.01 dejan de figurar como faltante del cliente en "
            + escape_html(deposit.name) + ". Se conservan efectivo, destinos, importes originales y documentación manual.")
        _touch_documentation(document)
    return True
