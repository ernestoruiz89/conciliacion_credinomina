"""Retain reconciliation evidence when a source reload is financially identical."""
from credinomina_reconciliation.rounding import money, decimal_value


TEXT_FIELDS = ('event_type', 'source_key', 'accounting_source_key', 'currency', 'equivalent_currency',
               'source_account', 'source_currency',
               'client_number', 'national_id', 'loan_number', 'installment_number',
               'reference', 'voucher', 'receipt', 'accounting_entry', 'processing_route',
               'historical_period', 'remittance_allocation', 'complementary_item')
STATE_FIELDS = (
    'application_adjustment_usd', 'net_applied_usd', 'application_adjustment_status',
    'historical_application_id', 'effective', 'match_status', 'match_reason',
    'collection_period', 'collection_row_id', 'application_allocation_detail',
    'deposit_match_status', 'deposit_match_reason', 'historical_remitted_usd',
    'historical_balance_usd', 'historical_detail', 'allocated_usd', 'unallocated_usd',
    'justified_surplus_usd', 'unclassified_usd', 'allocation_reason', 'allocation_detail',
    'inherited_exception_comment', 'complementary_usd', 'fx_variance_usd',
    'rounding_adjustment_usd', 'rounding_movement_detail', 'quality_basis', 'quality_status',
)


def reconciliation_state(previous, incoming):
    if not previous or previous.get('event_type') != 'Aplicacion' or incoming.get('event_type') != 'Aplicacion':
        return {}
    if any(str(previous.get(field) or '') != str(incoming.get(field) or '') for field in TEXT_FIELDS):
        return {}
    if str(previous.get('event_date') or '')[:10] != str(incoming.get('event_date') or '')[:10]:
        return {}
    if any(money(previous.get(field)) != money(incoming.get(field))
           for field in ('amount', 'equivalent_amount', 'source_debit', 'source_credit')):
        return {}
    if any(decimal_value(previous.get(field)) != decimal_value(incoming.get(field)) for field in ('manual_fx_rate', 'fx_rate')):
        return {}
    if bool(previous.get('effective')) != bool(incoming.get('effective')):
        return {}
    return {field: previous[field] for field in STATE_FIELDS if field in previous}
