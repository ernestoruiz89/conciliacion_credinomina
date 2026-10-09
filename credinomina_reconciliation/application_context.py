"""Permission-scoped inputs shared by application balance reports."""
from credinomina_reconciliation.report_records import records, child_records

IMPORTED_STATUSES = ('Importado', 'Importado con excepciones')


def load_application_context(employer=None, *, include_applications=True):
    # Dates are intentionally unrestricted: cash paid later still covers older
    # applications, and operational applications can share a collection row.
    scope = {'employer': employer} if employer else {}
    periods = {row.name: row for row in records('CN Reconciliation Period', fields=[
        'name', 'employer', 'payroll_month', 'collection_cycle', 'reconciliation_mode'], filters=scope)}
    imports = {row.name: row for row in records('CN Accounting Import',
        filters={**scope, 'status': ['in', list(IMPORTED_STATUSES)]},
        fields=['name', 'employer', 'historical_backfill', 'historical_period'])} if include_applications else {}
    sources = list(child_records('CN Source Row', imports, 'CN Accounting Import', 'rows',
        filters={'event_type': 'Aplicacion', 'effective': 1}, fields=[
            'name', 'parent', 'event_type', 'event_date', 'effective', 'match_status',
            'payment_due_date', 'payment_term_origin', 'client', 'client_name', 'client_number',
            'national_id', 'loan_number', 'installment_number', 'currency', 'amount',
            'equivalent_currency', 'equivalent_amount', 'fx_basis', 'manual_fx_rate',
            'processing_route', 'historical_period', 'portfolio_employer', 'collection_row_id',
            'application_allocation_detail', 'historical_remitted_usd', 'historical_detail',
            'application_adjustment_usd'])) if include_applications else []
    collections = {row.name: row for row in child_records('CN Collection Row', periods,
        'CN Reconciliation Period', 'collection_rows', fields=[
            'name', 'parent', 'row_key', 'client', 'client_name', 'client as client_number', 'national_id', 'loan_number',
            'installment_number', 'remittance_detail', 'rounding_adjustment_usd', 'fx_variance_usd'])} if include_applications else {}
    names = {row.employer for row in [*periods.values(), *imports.values()] if row.employer}
    names.update(row.portfolio_employer for row in sources if row.portfolio_employer)
    employers = {row.name: row for row in records('CN Employer',
        filters={'name': ['in', sorted(names)]}, fields=['name', 'grace_days'])} if names else {}
    return sources, imports, periods, collections, employers
