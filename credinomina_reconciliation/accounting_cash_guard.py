"""Keep the original application behind paid or reserved deposit destinations."""
import frappe
from frappe import _

from credinomina_reconciliation.application_adjustments import cash_coverage
from credinomina_reconciliation.application_context import IMPORTED_STATUSES
from credinomina_reconciliation.rounding import decimal_value, money
from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
from credinomina_reconciliation.paying_employers import reconciliation_companies

IDENTITY_FIELDS = ('event_type', 'event_date', 'source_key', 'accounting_source_key',
                   'currency', 'equivalent_currency', 'loan_number', 'client_number',
                   'national_id', 'accounting_entry', 'receipt', 'reference', 'voucher',
                   'effective', 'remittance_allocation', 'complementary_item',
                   'historical_period', 'processing_route')
AMOUNT_FIELDS = ('amount', 'equivalent_amount', 'manual_fx_rate', 'fx_rate')
DERIVED_FIELDS = ('match_status', 'quality_basis', 'quality_status', 'collection_period', 'collection_row_id',
                  'application_allocation_detail', 'historical_remitted_usd',
                  'historical_balance_usd', 'historical_detail')
DERIVED_AMOUNT_FIELDS = {'historical_remitted_usd', 'historical_balance_usd'}


def _derived_changed(old, row):
    # JSON from the form may send 100 for a persisted 100.0 Currency value.
    # Compare exact numeric values; formatting alone cannot change cash.
    return any(
        decimal_value(old.get(field)) != decimal_value(row.get(field))
        if field in DERIVED_AMOUNT_FIELDS
        else str(old.get(field) or '') != str(row.get(field) or '')
        for field in DERIVED_FIELDS
    )


def guard_cash_changes(document, previous=None, deleting=False, *, verified_reconciliation=False, unpaid_refresh=False):
    previous = previous if previous is not None else document.get_doc_before_save()
    if deleting:
        previous = document
    # Imported source data must not fabricate actual cash/distribution results.
    if not deleting and not verified_reconciliation and not unpaid_refresh:
        old_names = {row.name for row in previous.rows or []} if previous else set()
        for row in document.rows or []:
            if row.get('event_type') == 'Aplicacion' and row.name not in old_names and (
                money(row.get('historical_remitted_usd'))
                or row.get('historical_detail') not in (None, '', '[]')
                or row.get('application_allocation_detail') not in (None, '', '[]')
            ):
                frappe.throw(_('Los resultados de conciliación se calculan desde los vínculos reales. Use Conciliar período predeterminado; no indique depósitos asignados manualmente en la fila contable.'))
    if not previous:
        return
    current = {} if deleting else {row.name: row for row in document.rows or []}
    # Imported/with-exceptions is a harmless refresh. Leaving that population
    # would hide the debt while its confirmed or reserved cash still exists.
    visibility_changed = ((previous.get('status') in IMPORTED_STATUSES)
                          != (document.get('status') in IMPORTED_STATUSES))
    changed_rows = []
    for old in previous.rows or []:
        if old.get('event_type') != 'Aplicacion':
            continue
        row = current.get(old.name)
        changed = (not row or visibility_changed
            or (old.get('match_status') == 'Ignorado') != (row.get('match_status') == 'Ignorado')
            or any(str(previous.get(field) or '') != str(document.get(field) or '')
                                 for field in ('employer', 'source_file', 'file_hash', 'historical_backfill', 'historical_period'))
            or any(str(old.get(field) or '') != str(row.get(field) or '') for field in IDENTITY_FIELDS)
            or (not verified_reconciliation and _derived_changed(old, row))
            or any(decimal_value(old.get(field)) != decimal_value(row.get(field)) for field in AMOUNT_FIELDS))
        if not changed:
            continue
        changed_rows.append(old)
    if not changed_rows:
        return
    companies = {company for owner in {previous.get('employer'), document.get('employer')} - {None, ''}
                 for company in reconciliation_companies(owner)}
    # A reimport/delete must not pass an unpaid check while another request is
    # assigning its application. Reconciliation uses the same generation lock.
    lock_cash_pool(companies)
    for old in changed_rows:
        coverage = cash_coverage(old)
        if coverage['snapshots'] or money(coverage['protected_usd']) > 0:
            frappe.throw(_(
                'La aplicación {0} tiene dinero asignado o destinos reservados en depósitos. '
                'Desconcilie y retire esos destinos antes de borrar la fila, ocultarla de los reportes '
                'o cambiar su origen, identidad o importe.'
            ).format(old.get('idx') or old.name))
        row = current.get(old.name)
        if row and not verified_reconciliation and not unpaid_refresh and _derived_changed(old, row):
            frappe.throw(_('Los resultados de conciliación de la aplicación {0} se calculan desde sus vínculos reales. Use Conciliar período predeterminado para actualizarlos.').format(old.get('idx') or old.name))
