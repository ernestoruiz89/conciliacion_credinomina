"""Internal dependency checks: hidden evidence must never become unpaid debt.

Only a generic permission error may escape these checks, never hidden document
identifiers or amounts. Unrelated and post-cutoff evidence does not block a cut.
"""
import json

from credinomina_reconciliation.report_records import require_complete_evidence


def require_cutoff_evidence(context, deposits, items, cutoff):
    sources, imports, periods, collections, employers = context
    before = lambda value: not value or str(value)[:10] <= str(cutoff)
    source_ids = {row['name'] for row in sources if before(row.get('event_date'))}
    collection_keys = {(row['parent'], row.get('row_key')) for row in collections.values()}
    item_ids = {row['name'] for row in items}
    deposit_ids = {row['name'] for row in deposits}
    needed_deposits = {row.get('registered_deposit') for row in items} - {None, ''}
    needed_items = set(item_ids)
    claims = {'H:' + name for name in source_ids} | {'C:' + name for name in collections}
    registrations = {row.get('registration_exception') for row in items} - {None, ''}

    def journal(value):
        parsed = json.loads(value or '[]') if isinstance(value, str) else value or []
        if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
            raise ValueError('Invalid journal')
        return parsed

    for deposit in deposits:
        if deposit.get('docstatus') != 1 or not before(deposit.get('deposit_date')):
            continue
        try:
            for entry in journal(deposit.get('allocation_detail')):
                needed_items.update(value for value in (entry.get('partida'), entry.get('movimiento')) if value)
        except (ValueError, TypeError):
            pass  # The normal visible-evidence validation reports this journal.

    def relevant_item(row):
        from credinomina_reconciliation.application_adjustment_links import links
        # A later effective classification cannot change this cut, but original
        # accounting evidence may have an earlier source date.
        if not before(row.get('source_date') or row.get('posting_date')):
            return False
        return (row.name in needed_items or any(link.related_application in source_ids for link in links(row))
                or row.get('registered_deposit') in deposit_ids
                or row.get('receivable_origin') in item_ids or row.get('claim_id') in claims
                or row.get('accounting_exception') in registrations)

    require_complete_evidence('CN Complementary Item', item_ids,
        filters={'docstatus': ['!=', 2]}, relevant=relevant_item,
        fields=['source_date', 'posting_date', 'related_application', 'application_adjustment_history', 'registered_deposit',
                'receivable_origin', 'claim_id', 'accounting_exception'])

    def relevant_deposit(row):
        if row.name in needed_deposits:
            return True
        try:
            return any(entry.get('aplicacion_id') in source_ids
                       or (entry.get('periodo'), entry.get('fila_id')) in collection_keys
                       or entry.get('partida') in item_ids or entry.get('movimiento') in item_ids
                       for entry in journal(row.get('allocation_detail')))
        except (ValueError, TypeError):
            # Fail closed if a malformed hidden journal mentions a dependency.
            names = source_ids | item_ids | {key[0] for key in collection_keys}
            return any(json.dumps(name, ensure_ascii=ascii_only) in (row.get('allocation_detail') or '')
                       for name in names for ascii_only in (False, True))

    require_complete_evidence('CN Remittance Allocation', deposit_ids,
        filters={'docstatus': 1, 'deposit_date': ['<=', str(cutoff)]},
        fields=['allocation_detail'], relevant=relevant_deposit)
