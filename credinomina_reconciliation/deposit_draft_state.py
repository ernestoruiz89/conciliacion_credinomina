"""A draft has planned inputs, never an executed reconciliation inherited by copy."""


def reset_unexecuted(document, previous=None):
    """Keep cancelled audit snapshots and existing submitted executions intact."""
    if document.get('docstatus') == 2 or (previous and previous.get('docstatus') != 0):
        return False
    document.update(dict(allocated_usd=0, justified_surplus_usd=0,
        unallocated_usd=0, unclassified_usd=0, allocation_detail='[]', result='Pendiente',
        inherited_exception_comment='', credit_coverage_usd=0, complementary_coverage_usd=0,
        detail_status='Cargado; pendiente de conciliación' if document.get('detail_rows') else 'Detalle pendiente'))
    for target in document.get('targets') or []:
        target.update(dict(result='Pendiente'))
    for row in document.get('detail_rows') or []:
        row.update(dict(linked_usd=0, client_credit_usd=0, company_credit_usd=0, pending_usd=0,
            matched_targets='[]', matched_targets_summary='', match_status='Pendiente', match_reason=''))
    return True
