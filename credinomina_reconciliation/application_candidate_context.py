"""Batched, request-local preview only. Confirmation always uses locked live reads."""
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.report_records import records


def entries(value):
    return frappe.parse_json(value or '[]')


def batches(values, size=500):
    values = sorted(set(values) - {None, ''})
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


class CandidateContext:
    def __init__(self, rows, parent, item):
        self.parent = parent
        self.rows = rows
        self.saved_collections = entries(item.get('adjustment_collection_rows'))
        self.saved_periods = entries(item.get('adjustment_periods'))
        collection_ids = set(self.saved_collections)
        for row in rows:
            collection_ids.update(self.collection_ids(row))
        self.collections = {}
        for names in batches(collection_ids):
            self.collections.update({row.name: row for row in frappe.get_all('CN Collection Row',
                filters={'name': ['in', names], 'parenttype': 'CN Reconciliation Period', 'parentfield': 'collection_rows'},
                fields=['name', 'parent', 'row_key', 'applied_usd'], limit_page_length=0)})
        item_ids = {entry.get('partida') for row in rows for entry in entries(row.get('allocation_detail')) if isinstance(entry, dict)}
        self.item_periods = {}
        for names in batches(item_ids):
            self.item_periods.update({row.name: row.period for row in frappe.get_all('CN Complementary Item',
                filters={'name': ['in', names]}, fields=['name', 'period'], limit_page_length=0)})
        period_ids = set(self.saved_periods) | {parent.get('historical_period')}
        period_ids.update(collection.parent for collection in self.collections.values())
        for row in rows:
            period_ids.update(self.linked_periods(row))
        self.statuses = {}
        for names in batches(period_ids):
            self.statuses.update({row.name: row.status for row in frappe.get_all('CN Reconciliation Period',
                filters={'name': ['in', names]}, fields=['name', 'status'], limit_page_length=0)})
        self.reserved_item = bool(frappe.db.exists('CN Remittance Target',
            {'complementary_item': item.name, 'docstatus': ['<', 2]}))
        self.readable_periods, self.readable_companies = {}, set()
        if not parent.get('employer'):
            for names in batches(period_ids):
                self.readable_periods.update({row.name: row.employer for row in records('CN Reconciliation Period',
                    filters={'name': ['in', names]}, fields=['name', 'employer'])})
            for names in batches(row.get('portfolio_employer') for row in rows):
                self.readable_companies.update(row.name for row in records('CN Employer',
                    filters={'name': ['in', names]}, fields=['name']))
        self.reserved = defaultdict(lambda: money(0))
        self._load_cash()

    def collection_ids(self, row):
        return {row.get('collection_row_id'), *[link.get('collection_row_id')
            for link in entries(row.get('application_allocation_detail')) if isinstance(link, dict)]} - {None, ''}

    def linked_periods(self, row):
        periods = {row.get('historical_period'), row.get('collection_period')}
        for entry in entries(row.get('application_allocation_detail')):
            if isinstance(entry, dict):
                periods.add(entry.get('period') or entry.get('periodo'))
        for collection in self.collection_ids(row):
            periods.add(self.collections.get(collection, {}).get('parent'))
        for entry in entries(row.get('allocation_detail')):
            if isinstance(entry, dict):
                periods.add(entry.get('periodo') or entry.get('period'))
                periods.add(self.item_periods.get(entry.get('partida')))
        return periods - {None, ''}

    def employer(self, row):
        if self.parent.get('employer'):
            return self.parent.employer
        period = row.get('historical_period') or self.parent.get('historical_period')
        if not period and row.get('collection_row_id'):
            period = self.collections.get(row.collection_row_id, {}).get('parent')
        if period:
            return self.readable_periods.get(period) or ''
        company = row.get('portfolio_employer')
        return company if company in self.readable_companies else ''

    def block_reason(self, row, saved_periods=(), saved_collections=()):
        periods = self.linked_periods(row) | set(saved_periods)
        periods.update(self.collections.get(name, {}).get('parent') for name in saved_collections)
        closed = sorted(name for name in periods if self.statuses.get(name) == 'Cerrado')
        if closed:
            return _('Reabra el período {0} antes de confirmar o cancelar el ajuste.').format(closed[0])
        if self.reserved_item:
            return _('Una partida de ajuste no puede ser también destino de un depósito. Retire ese destino antes de confirmar o cancelar el ajuste.')
        return None

    def _load_cash(self):
        keys = {(row.parent, row.row_key): name for name, row in self.collections.items()}
        applications = {row.name for row in self.rows}
        planned, paid = defaultdict(lambda: money(0)), defaultdict(lambda: money(0))
        targets = {}
        for field, names in [('historical_application', applications), ('period', {key[0] for key in keys})]:
            for batch in batches(names):
                for row in frappe.get_all('CN Remittance Target', filters={field: ['in', batch], 'docstatus': ['<', 2]},
                        fields=['name', 'parent', 'historical_application', 'period', 'row_key', 'amount_usd'], limit_page_length=0):
                    targets[row.name] = row
        for row in targets.values():
            # A target has exactly one destination; do not count the same target
            # twice if a direct application is also projected to a collection.
            claim = (('H', row.historical_application) if row.historical_application in applications
                     else ('C', keys.get((row.period, row.row_key))))
            if claim[1]:
                planned[(row.parent, claim)] += money(row.amount_usd)
        seen = set()
        for batch in batches(applications | {key[0] for key in keys}, size=100):
            # Escape LIKE metacharacters. JSON parsing below verifies exact IDs.
            patterns = [name.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') for name in batch]
            for deposit in frappe.get_all('CN Remittance Allocation', filters={'docstatus': 1},
                    or_filters=[['allocation_detail', 'like', '%' + pattern + '%'] for pattern in patterns],
                    fields=['name', 'allocation_detail'], limit_page_length=0):
                if deposit.name in seen:
                    continue
                seen.add(deposit.name)
                for entry in entries(deposit.allocation_detail):
                    claim = (('H', entry.get('aplicacion_id')) if entry.get('tipo') == 'Aplicacion historica'
                             and entry.get('aplicacion_id') in applications else
                             ('C', keys.get((entry.get('periodo'), entry.get('fila_id'))))
                             if entry.get('tipo') == 'Cobranza' else None)
                    if claim and claim[1]:
                        paid[(deposit.name, claim)] += money(entry.get('importe_usd'))
        for key in paid.keys() | planned.keys():
            self.reserved[key[1]] += max(paid[key], planned[key])
        from credinomina_reconciliation.credit_application import funding_totals
        for application, amount in funding_totals(applications).items():
            self.reserved[('H', application)] += amount

    def coverage(self, row, saved_collections=()):
        from credinomina_reconciliation.application_adjustments import coverage_from_reservations
        wanted = self.collection_ids(row) | set(saved_collections)
        return coverage_from_reservations(row, {name: self.collections[name] for name in wanted if name in self.collections}, self.reserved)
