"""USD period coverage, including confirmed offsets without counting them twice."""
import json
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _

from credinomina_reconciliation.reconciliation import converted_amount
from credinomina_reconciliation.rounding import money, money_float, sum_money


_context = ContextVar("cn_period_totals", default=None)
TOTAL_FIELDS = ("applied_total_usd", "remitted_total_usd", "pending_usd")


def entries(value):
    result = json.loads(value or "[]") if isinstance(value, str) else value or []
    return result if isinstance(result, list) else []


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
    """
    reduction = money(application_adjustment_usd)
    applied = money(period.get("applied_usd")) + reduction
    covered = reduction
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
        settled = money(cash) - money(rounding)
        covered += settled
        pending += max(money(net) - settled, money(0))
    return dict(zip(TOTAL_FIELDS, map(money_float, (applied, covered, pending))))


class PeriodTotalsContext:
    """Reuse current reconciliation rows before their parents are saved.

    Ordinary saves and migration read persisted evidence. The engine supplies
    in-memory rows so totals never lag one reconciliation behind.
    """
    def __init__(self, periods, source_rows=None):
        self.periods = {period.name: period for period in periods}
        self.source_rows = source_rows
        self.historical = None
        self.reductions = None

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
        self.reductions = defaultdict(lambda: money(0))
        employers = sorted({period.get("employer") for period in self.periods.values() if period.get("employer")})
        adjustments = frappe.get_all("CN Complementary Item", filters={
            "docstatus": 1, "category": "Ajuste de aplicación", "employer": ["in", employers],
        }, fields=["period", "related_application", "application_adjustment_usd", "adjustment_periods"],
            limit_page_length=0) if employers else []
        for item in adjustments:
            # Use the item's explicit period; never repeat a reduction in every
            # period of an application shared across two payroll halves.
            source = by_name.get(item.related_application)
            saved = entries(item.get("adjustment_periods"))
            period_name = item.period or (source.get("historical_period") if source else None)
            if not period_name and len(saved) == 1:
                period_name = saved[0]
            if period_name and period_name in self.periods:
                self.reductions[period_name] += money(item.application_adjustment_usd)

    def values(self, period):
        self._load()
        direct = self.historical[period.name]
        result = calculate_totals(period, direct, self.reductions[period.name])
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
