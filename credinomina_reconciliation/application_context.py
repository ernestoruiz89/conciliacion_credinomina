"""Permission-scoped inputs shared by application balance reports."""
from credinomina_reconciliation.report_records import records, child_records


def load_application_context():
    periods = {row.name: row for row in records('CN Reconciliation Period', fields=[
        'name', 'employer', 'payroll_month', 'collection_cycle', 'reconciliation_mode'])}
    imports = {row.name: row for row in records('CN Accounting Import',
        filters={'status': ['in', ['Importado', 'Importado con excepciones']]},
        fields=['name', 'employer', 'historical_backfill', 'historical_period'])}
    sources = list(child_records('CN Source Row', imports, 'CN Accounting Import', 'rows',
        filters={'event_type': 'Aplicacion', 'effective': 1}, fields=[
            'name', 'parent', 'event_type', 'event_date', 'effective', 'match_status',
            'payment_due_date', 'payment_term_origin', 'client', 'client_name', 'client_number',
            'national_id', 'loan_number', 'installment_number', 'currency', 'amount',
            'equivalent_currency', 'equivalent_amount', 'fx_basis', 'manual_fx_rate',
            'processing_route', 'historical_period', 'portfolio_employer', 'collection_row_id',
            'application_allocation_detail', 'historical_remitted_usd', 'historical_detail',
            'application_adjustment_usd']))
    collections = {row.name: row for row in child_records('CN Collection Row', periods,
        'CN Reconciliation Period', 'collection_rows', fields=[
            'name', 'parent', 'client', 'client_name', 'client_number', 'national_id', 'loan_number',
            'installment_number', 'remittance_detail', 'rounding_adjustment_usd', 'fx_variance_usd'])}
    names = {row.employer for row in [*periods.values(), *imports.values()] if row.employer}
    names.update(row.portfolio_employer for row in sources if row.portfolio_employer)
    employers = {row.name: row for row in records('CN Employer',
        filters={'name': ['in', sorted(names)]}, fields=['name', 'grace_days'])} if names else {}
    return sources, imports, periods, collections, employers
