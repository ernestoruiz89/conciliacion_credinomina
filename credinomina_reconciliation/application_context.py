"""Permission-scoped inputs shared by application balance reports."""
from credinomina_reconciliation.report_records import records, child_records

IMPORTED_STATUSES = ('Importado', 'Importado con excepciones')


def import_company_scope(employer=None):
    """Include legacy parents; their company must be checked at row level."""
    return ({'or_filters': [['employer', '=', employer], ['employer', 'is', 'not set']]}
            if employer else {})


def application_employer(source, parent, periods, collections=None):
    """Use the same attribution as application balances, without guessing aliases."""
    period = periods.get(source.get('historical_period') or parent.get('historical_period'))
    if period:
        return period.get('employer') or ''
    if parent.get('employer'):
        return parent.get('employer')
    collection = (collections or {}).get(source.get('collection_row_id'))
    if collection and collection.get('parent') in periods:
        return periods[collection['parent']].get('employer') or ''
    return source.get('portfolio_employer') or ''


def linked_application_employer(source, parent):
    """Resolve legacy blank headers for write validation using readable evidence."""
    if parent.get('employer'):
        return parent.get('employer')
    period_name = source.get('historical_period') or parent.get('historical_period')
    if not period_name and source.get('collection_row_id'):
        # The source row was read through its permission-checked import.
        import frappe
        period_name = frappe.db.get_value('CN Collection Row', source.get('collection_row_id'), 'parent')
    if period_name:
        periods = list(records('CN Reconciliation Period', filters={'name': period_name}, fields=['name', 'employer']))
        return periods[0].get('employer') or '' if periods else ''
    company = source.get('portfolio_employer')
    if company:
        employers = list(records('CN Employer', filters={'name': company}, fields=['name']))
        return company if employers else ''
    return ''


def load_application_context(employer=None, *, include_applications=True, include_current_settlements=True):
    """Load readable claims; omit current cash only when the caller reconstructs it."""
    # Dates are intentionally unrestricted: cash paid later still covers older
    # applications, and operational applications can share a collection row.
    scope = {'employer': employer} if employer else {}
    periods = {row.name: row for row in records('CN Reconciliation Period', fields=[
        'name', 'employer', 'payroll_month', 'collection_cycle', 'reconciliation_mode'], filters=scope)}
    imports = {row.name: row for row in records('CN Accounting Import',
        filters={'status': ['in', list(IMPORTED_STATUSES)]},
        **import_company_scope(employer),
        fields=['name', 'employer', 'status', 'historical_backfill', 'historical_period'])} if include_applications else {}
    # Older imported files may not identify the company on the parent. Their
    # rows/periods still do; do not lose them when drilling down from the total.
    sources = list(child_records('CN Source Row', imports, 'CN Accounting Import', 'rows',
        filters={'event_type': 'Aplicacion', 'effective': 1}, fields=[
            'name', 'parent', 'event_type', 'event_date', 'effective', 'match_status',
            'payment_due_date', 'payment_term_origin', 'client', 'client_name', 'client_number',
            'difference_reason',
            'national_id', 'loan_number', 'installment_number', 'currency', 'amount',
            'equivalent_currency', 'equivalent_amount', 'fx_basis', 'manual_fx_rate',
            'processing_route', 'historical_period', 'portfolio_employer', 'collection_row_id',
            'application_allocation_detail',
            *(['historical_remitted_usd', 'historical_detail'] if include_current_settlements else []),
            'application_adjustment_usd'])) if include_applications else []
    collections = {row.name: row for row in child_records('CN Collection Row', periods,
        'CN Reconciliation Period', 'collection_rows', fields=[
            'name', 'parent', 'row_key', 'client', 'client_name', 'client as client_number', 'national_id', 'loan_number',
            'installment_number', 'applied_usd',
            *(['remittance_detail', 'rounding_adjustment_usd', 'fx_variance_usd']
              if include_current_settlements else [])])} if include_applications else {}
    names = {row.employer for row in [*periods.values(), *imports.values()] if row.employer}
    names.update(row.portfolio_employer for row in sources if row.portfolio_employer)
    employers = {row.name: row for row in records('CN Employer',
        filters={'name': ['in', sorted(names)]}, fields=['name', 'grace_days'])} if names else {}
    return sources, imports, periods, collections, employers
