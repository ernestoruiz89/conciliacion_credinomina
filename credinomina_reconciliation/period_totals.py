"""USD period coverage, including confirmed offsets without counting them twice."""
import json
import re
from collections import defaultdict
from decimal import Decimal
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _

from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.rounding import money, money_float, sum_money


_context = ContextVar("cn_period_totals", default=None)
TOTAL_FIELDS = ("applied_total_usd", "deposit_coverage_usd", "complementary_coverage_usd",
                "remitted_total_usd", "pending_usd")


def entries(value):
    result = json.loads(value or "[]") if isinstance(value, str) else value or []
    return result if isinstance(result, list) else []


def _mentions_record(raw, name):
    """Identify complete record names even when a journal cannot be decoded."""
    if not name:
        return False
    # Names can contain separators such as dashes, slashes and dots. Treat
    # those as part of the token so P1 never matches P10 or P1-OTHER.
    return re.search(r"(?<![\w@./:+-])" + re.escape(str(name)) +
                     r"(?![\w@./:+-])", str(raw)) is not None


def _credit_funding(historical_rows):
    """Read executed credit funding once, without its collection projection."""
    total, operations = money(0), set()
    for source in historical_rows:
        detail = entries(source.get('historical_detail'))
        funding = [entry for entry in detail if isinstance(entry, dict) and
                   (entry.get('origen') == 'Saldo a favor' or entry.get('saldo_a_favor'))]
        if not funding:
            continue
        try:
            amounts = [Decimal(str(entry.get('importe_usd'))) for entry in detail]
            if any(not amount.is_finite() for amount in amounts):
                raise ValueError()
            if sum_money(amounts) != money(source.get('historical_remitted_usd')):
                raise ValueError()
            for entry in funding:
                amount = Decimal(str(entry.get('importe_usd')))
                if (not entry.get('saldo_a_favor') or not entry.get('operacion') or
                        entry.get('origen') != 'Saldo a favor' or amount < 0):
                    raise ValueError()
                operation = (entry['saldo_a_favor'], entry['operacion'])
                if operation in operations:
                    raise ValueError()
                operations.add(operation)
                total += money(amount)
        except (TypeError, ValueError, ArithmeticError, AttributeError):
            return None
    return total


def direct_amounts_by_collection(sources):
    result = defaultdict(lambda: money(0))
    for source in sources:
        links = entries(source.get("application_allocation_detail"))
        if not links and source.get("collection_row_id"):
            links = [{"collection_row_id": source.get("collection_row_id"), "amount_usd": converted_amount(source, "USD")}]
        for link in links:
            result[link.get("collection_row_id")] += money(link.get("amount_usd"))
    return result


def calculate_totals(period, historical_rows=(), application_adjustment_usd=0):
    """Keep cash to fees/credits separate from coverage of core applications.

    Applied balances already contain the USD conversion and confirmed reductions.
    Restore those reductions on both sides to display original applied vs covered.
    Signed tolerance is removed from cash once; FX under review is not coverage.
    Show actual cash under deposits and the signed difference under complementary
    coverage so their sum remains the existing covered total.
    """
    reduction = money(application_adjustment_usd)
    applied = money(period.get("applied_usd")) + reduction
    covered = reduction
    deposits = money(0)
    rounding_total = money(0)
    pending = money(0)
    if period.get("reconciliation_mode") == "Historica":
        claims = [
            (converted_amount(row, "USD"), row.get("historical_remitted_usd"),
             sum_money(entry.get("diferencia_usd") for entry in entries(row.get("historical_detail"))))
            for row in historical_rows
        ]
    else:
        direct = direct_amounts_by_collection(historical_rows)
        claims = [
            (max(money(row.get("applied_usd")) - direct[row.get("name")], 0), sum_money(
                entry.get("importe_usd") for entry in entries(row.get("remittance_detail"))
                if entry.get("destino") != "Partida complementaria" and not entry.get("aplicacion_directa")
            ), money(row.get("rounding_adjustment_usd")) - sum_money(
                entry.get("diferencia_usd") for entry in entries(row.get("remittance_detail")) if entry.get("aplicacion_directa")))
            for row in period.get("collection_rows") or []
        ]
        claims.extend((converted_amount(row, "USD"), row.get("historical_remitted_usd"),
                       sum_money(entry.get("diferencia_usd") for entry in entries(row.get("historical_detail"))))
                      for row in historical_rows)
        if historical_rows:
            applied = sum_money(claim[0] for claim in claims) + reduction
    for net, cash, rounding in claims:
        if net is None:
            frappe.throw(_("No se puede calcular el resumen del período: falta la conversión de una aplicación a US$."))
        deposits += money(cash)
        rounding_total += money(rounding)
        settled = money(cash) - money(rounding)
        covered += settled
        pending += max(money(net) - settled, money(0))
    complementary = reduction - rounding_total
    return dict(zip(TOTAL_FIELDS, map(money_float, (applied, deposits, complementary, covered, pending))))


class PeriodTotalsContext:
    """Reuse current reconciliation rows before their parents are saved.

    Ordinary saves and migration read persisted evidence. The engine supplies
    in-memory rows so totals never lag one reconciliation behind.
    """
    def __init__(self, periods, source_rows=None, cash_allocation=None):
        self.periods = {period.name: period for period in periods}
        self.source_rows = source_rows
        self.historical = None
        self.reductions = None
        self.cash_allocation = cash_allocation
        self.cash_breakdowns = None

    @contextmanager
    def use(self):
        token = _context.set(self)
        try:
            yield
        finally:
            _context.reset(token)

    def _load(self):
        if self.historical is not None:
            return
        sources = self.source_rows
        if sources is None:
            names = list(self.periods)
            sources = frappe.get_all("CN Source Row", filters={
                "parenttype": "CN Accounting Import", "historical_period": ["in", names],
                "event_type": "Aplicacion", "effective": 1, "match_status": "Conciliado",
            }, fields=["name", "historical_period", "event_type", "effective", "match_status",
                       "currency", "amount", "equivalent_currency", "equivalent_amount",
                       "manual_fx_rate", "fx_basis", "application_adjustment_usd",
                       "historical_remitted_usd", "historical_detail", "collection_row_id",
                       "application_allocation_detail"], limit_page_length=0) if names else []
        self.historical = defaultdict(list)
        by_name = {}
        for row in sources:
            if row.get("event_type") != "Aplicacion" or not row.get("effective"):
                continue
            by_name[row.name] = row
            if row.get("historical_period") in self.periods and row.get("match_status") == "Conciliado":
                self.historical[row.historical_period].append(row)
        self.sources_by_name = by_name
        self.reductions = defaultdict(lambda: money(0))
        employers = sorted({period.get("employer") for period in self.periods.values() if period.get("employer")})
        adjustments = frappe.get_all("CN Complementary Item", filters={
            "docstatus": 1, "category": "Ajuste de aplicación", "employer": ["in", employers],
        }, fields=["period", "related_application", "application_adjustment_usd", "adjustment_periods", "application_adjustment_history"],
            limit_page_length=0) if employers else []
        from credinomina_reconciliation.application_adjustment_links import links
        portions = [portion for adjustment in adjustments for portion in links(adjustment)]
        current_periods = {name: source.get("historical_period") for name, source in by_name.items()}
        # Confirmed adjustment metadata records the original scope. A historical
        # application may have been reassigned since then. Resolve missing rows
        # that would otherwise leave an offset behind when saving its old period.
        missing = set()
        for item in portions:
            saved = entries(item.get("adjustment_periods"))
            previous_period = item.period or (saved[0] if len(saved) == 1 else None)
            if item.related_application not in current_periods and previous_period in self.periods:
                missing.add(item.related_application)
        ordered = sorted(missing)
        for offset in range(0, len(ordered), 500):
            current_periods.update({row.name: row.historical_period for row in frappe.get_all(
                "CN Source Row", filters={"parenttype": "CN Accounting Import", "event_type": "Aplicacion",
                    "name": ["in", ordered[offset:offset + 500]]},
                fields=["name", "historical_period"], limit_page_length=0,
            )})
        for item in portions:
            # Direct applications have one current period. For payroll links,
            # retain the explicit period rather than repeating the reduction
            # across every payroll half sharing an application.
            saved = entries(item.get("adjustment_periods"))
            period_name = current_periods.get(item.related_application) or item.period
            if not period_name and len(saved) == 1:
                period_name = saved[0]
            if period_name and period_name in self.periods:
                self.reductions[period_name] += money(item.application_adjustment_usd)

    def _load_cash_breakdowns(self):
        if self.cash_breakdowns is not None:
            return
        from credinomina_reconciliation.period_cash_breakdown import calculate
        claim_periods = {'H:' + name: row.get('historical_period') for name, row in self.sources_by_name.items()
                         if row.get('effective') and row.get('match_status') == 'Conciliado'}
        claim_periods.update({'C:' + row.name: period.name for period in self.periods.values()
                             for row in period.get('collection_rows') or []})
        allocations, errors = [], {}
        if self.cash_allocation is not None:
            # The selected deposit has not necessarily been persisted yet.
            # Use the complete in-flight journal, including frozen other cash.
            allocations = list(self.cash_allocation.get('allocations') or [])
            allocations += [dict(deposit_id=row['deposit_id'], claim_id='T:' + str(row.get('name') or index),
                                 amount_usd=row.get('consumed_residual_usd') or 0)
                            for index, row in enumerate(self.cash_allocation.get('rounding_movements') or [])]
        else:
            from credinomina_reconciliation.report_records import _pages
            for deposit in _pages(frappe.get_all, 'CN Remittance Allocation',
                    filters={'docstatus': 1},
                    or_filters=[['allocation_detail', 'like', '%Partida complementaria%'],
                                ['allocation_detail', 'like', '%Diferencia documentada de empresa%']],
                    fields=['name', 'allocation_detail', 'cash_identity_status']):
                if deposit.get('cash_identity_status') == 'Origen vinculado':
                    continue
                raw = deposit.get('allocation_detail') or '[]'
                try:
                    rows = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                        raise ValueError()
                    amounts = [Decimal(str(row.get('importe_usd'))) for row in rows]
                    if any(not amount.is_finite() for amount in amounts):
                        raise ValueError()
                    if not any(row.get('tipo') in {'Partida complementaria', 'Diferencia documentada de empresa'} and amount < 0
                               for row, amount in zip(rows, amounts)):
                        continue
                except (TypeError, ValueError, ArithmeticError):
                    for name in self.periods:
                        if _mentions_record(raw, name) or any(_mentions_record(raw, source.get('name'))
                                                               for source in self.historical[name]):
                            errors[name] = 'El diario del depósito compartido no permite comprobar el desglose de efectivo.'
                    continue
                # Keep whole deposits, never only the requested period's lines.
                relevant = any(row.get('periodo') in self.periods or
                               row.get('aplicacion_id') in self.sources_by_name for row in rows)
                if not relevant:
                    continue
                for index, row in enumerate(rows):
                    kind = row.get('tipo')
                    if kind == 'Aplicacion historica':
                        claim = 'H:' + str(row.get('aplicacion_id') or '')
                    elif kind == 'Cobranza':
                        claim = 'C:JOURNAL:' + json.dumps([row.get('periodo'), row.get('fila_id')])
                        claim_periods[claim] = row.get('periodo')
                    elif kind == 'Partida complementaria':
                        claim = 'X:' + str(row.get('partida') or '')
                    elif kind == 'Diferencia documentada de empresa':
                        claim = 'X:CONVERSION:' + deposit.name
                    else:
                        claim = 'T:' + str(row.get('movimiento') or index)
                    allocations.append(dict(deposit_id=deposit.name, claim_id=claim, amount_usd=row.get('importe_usd'),
                                            _period_hint=row.get('periodo') if kind in {'Aplicacion historica', 'Cobranza'} else None))
        try:
            amounts = [Decimal(str(row.get('amount_usd'))) for row in allocations]
            if any(not amount.is_finite() for amount in amounts):
                raise ValueError()
            negative_deposits = {row['deposit_id'] for row, amount in zip(allocations, amounts)
                                 if money(amount) < 0}
        except (TypeError, ValueError, ArithmeticError, AttributeError, KeyError):
            self.cash_breakdowns = {name: calculate(name, allocations, claim_periods) for name in self.periods}
            return
        allocations = [row for row in allocations if row['deposit_id'] in negative_deposits]
        # Resolve other beneficiaries too: missing metadata cannot be assumed
        # to belong to the selected period, even for a shared bank deposit.
        for prefix, doctype, fields in (
                ('H:', 'CN Source Row', ['name', 'historical_period', 'effective', 'match_status']),
                ('C:', 'CN Collection Row', ['name', 'parent'])):
            names = sorted({row['claim_id'][2:] for row in allocations
                            if row['claim_id'].startswith(prefix) and row['claim_id'] not in claim_periods})
            for offset in range(0, len(names), 500):
                filters = {'name': ['in', names[offset:offset + 500]],
                           'parenttype': 'CN Accounting Import' if prefix == 'H:' else 'CN Reconciliation Period',
                           'parentfield': 'rows' if prefix == 'H:' else 'collection_rows'}
                for row in frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=0):
                    if prefix == 'C:' or row.get('effective') and row.get('match_status') == 'Conciliado':
                        claim_periods[prefix + row.name] = row.get('historical_period') if prefix == 'H:' else row.parent
        # Scope whole deposits, not individual portions. In particular, a lost
        # application explicitly recorded in P1 must not make unrelated P2's
        # result depend on whether both periods share the same migration batch.
        deposit_scopes = defaultdict(set)
        unscoped = set()
        for row in allocations:
            if money(row.get('amount_usd')) <= 0 or row['claim_id'].partition(':')[0] not in {'H', 'C'}:
                continue
            current = claim_periods.get(row['claim_id'])
            hint = row.get('_period_hint')
            if current:
                deposit_scopes[row['deposit_id']].add(current)
            if hint:
                deposit_scopes[row['deposit_id']].add(hint)
            if not current and not hint:
                # The unresolved beneficiary could be any selected period.
                # A known co-beneficiary must not conceal that uncertainty.
                unscoped.add(row['deposit_id'])
        self.cash_breakdowns = {name: calculate(name, [row for row in allocations
            if row['deposit_id'] in unscoped or not deposit_scopes[row['deposit_id']]
            or name in deposit_scopes[row['deposit_id']]], claim_periods) for name in self.periods}
        for name, reason in errors.items():
            self.cash_breakdowns[name] = dict(known=False, reason=reason)

    def values(self, period):
        self._load()
        direct = self.historical[period.name]
        result = calculate_totals(period, direct, self.reductions[period.name])
        self._load_cash_breakdowns()
        breakdown = self.cash_breakdowns[period.name]
        delta = breakdown.get('cash_adjustment_usd')
        funding = _credit_funding(direct)
        known = (breakdown['known'] and funding is not None and
                 money(result['deposit_coverage_usd']) + money(delta) - funding >= 0)
        if known:
            conversion = money(breakdown.get('conversion_coverage_usd'))
            result['deposit_coverage_usd'] = money_float(money(result['deposit_coverage_usd']) + money(delta) - funding)
            result['complementary_coverage_usd'] = money_float(money(result['complementary_coverage_usd']) - money(delta) + funding - conversion)
            result['negative_complementary_usd'] = breakdown['negative_complementary_usd']
            result['conversion_coverage_usd'] = money_float(conversion)
        else:
            result.update(deposit_coverage_usd=None, complementary_coverage_usd=None, negative_complementary_usd=None, conversion_coverage_usd=None)
        result['cash_breakdown_known'] = int(known)
        result['cash_breakdown_note'] = '' if known else (breakdown.get('reason') or
            ('El detalle del saldo a favor aplicado está incompleto o duplicado; no permite comprobar el efectivo.'
             if funding is None else
             'La distribución guardada no coincide con la cobertura del período; revise su desglose.'))
        if period.get("reconciliation_mode") != "Historica" and direct:
            projected_cash = sum_money(entry.get("importe_usd")
                for row in period.get("collection_rows") or []
                for entry in entries(row.get("remittance_detail")) if entry.get("aplicacion_directa"))
            result["applied_usd"] = money_float(money(result["applied_total_usd"]) - self.reductions[period.name])
            result["remitted_usd"] = money_float(sum_money(row.get("remitted_usd") for row in period.get("collection_rows") or [])
                                                 - projected_cash + sum_money(row.get("historical_remitted_usd") for row in direct))
        return result


def update_period_totals(period):
    context = _context.get()
    if context is None or period.name not in context.periods:
        context = PeriodTotalsContext([period])
    for field, value in context.values(period).items():
        period.set(field, value)
