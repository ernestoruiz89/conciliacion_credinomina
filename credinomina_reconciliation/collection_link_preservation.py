"""Keep an existing collection destination's identity behind its financial links."""
import json
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.collection_identity import collection_client_number
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.rounding import money


def _identity(row):
    return dict(client=canonical_identifier(collection_client_number(row)),
        loan_number=canonical_credit_number(row.get('loan_number')),
        employee_number=canonical_identifier(row.get('employee_number')),
        national_id=canonical_identifier(row.get('national_id')),
        installment_number=canonical_identifier(row.get('installment_number')),
        row_key=clean_text(row.get('row_key')))


def conflicts(application, collection):
    """Completing missing data is admissible; contradictory identities are not."""
    left = {field: application.get(field) for field in
            ('loan_number', 'employee_number', 'national_id', 'installment_number')}
    left['client'] = application.get('client') or application.get('client_number')
    before, after = _identity(left), _identity(collection)
    return any(before[field] and after[field] and before[field] != after[field]
               for field in before if field != 'row_key')


def guard(period):
    seen = set()
    for row in period.get('collection_rows') or []:
        key = clean_text(row.get('row_key'))
        if key and key in seen:
            frappe.throw(_('La cobranza contiene la Fila ID duplicada {0}. Cada cuota debe tener un '
                'identificador distinto para conservar sus aplicaciones y depósitos.').format(key))
        if key:
            seen.add(key)
    previous = period.get_doc_before_save()
    if not previous:
        return
    current = {row.name: row for row in period.get('collection_rows') or []}
    changed = {}
    for old in previous.get('collection_rows') or []:
        row = current.get(old.name)
        before, after = _identity(old), _identity(row or {})
        if (not row or period.get('employer') != previous.get('employer')
                or any(value and value != after[field] for field, value in before.items())):
            changed[old.name] = old
    if not changed:
        return
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    owners = {previous.get('employer'), period.get('employer')} - {None, ''}
    lock_cash_pool({company for owner in owners for company in reconciliation_companies(owner)})
    protected = {name for name, row in changed.items() if any(money(row.get(field)) for field in
        ('applied_usd', 'complementary_usd', 'remitted_usd', 'rounding_adjustment_usd', 'fx_variance_usd'))}
    # Read actual links as well: old cached row amounts can be zero or stale.
    remaining = sorted(set(changed) - protected)
    for offset in range(0, len(remaining), 100):
        names = remaining[offset:offset + 100]
        sources = frappe.get_all('CN Source Row', filters={'parenttype': 'CN Accounting Import',
            'event_type': 'Aplicacion'}, or_filters=[['collection_row_id', 'in', names],
                *[['application_allocation_detail', 'like', '%' + name + '%'] for name in names]],
            fields=['collection_row_id', 'application_allocation_detail'], limit_page_length=0)
        for source in sources:
            if source.get('collection_row_id') in names:
                protected.add(source.collection_row_id)
            raw = source.get('application_allocation_detail') or '[]'
            try:
                links = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
                    raise ValueError()
                protected.update(link['collection_row_id'] for link in links
                                 if link.get('collection_row_id') in names)
            except (ValueError, TypeError):
                protected.update(name for name in names if name in str(raw))
    keys = defaultdict(set)
    for name, row in changed.items():
        keys[clean_text(row.get('row_key'))].add(name)
    if set(changed) - protected and keys:
        for target in frappe.get_all('CN Remittance Target', filters={'parenttype': 'CN Remittance Allocation',
                'docstatus': 1, 'period': previous.name},
                fields=['row_key'], limit_page_length=0):
            protected.update(keys.get(clean_text(target.get('row_key')), ()))
    if set(changed) - protected and keys:
        # Legacy executed cash may have no remaining planned target/source link.
        for deposit in frappe.get_all('CN Remittance Allocation', filters={'docstatus': 1,
                'allocation_detail': ['like', '%' + previous.name + '%']},
                fields=['allocation_detail'], limit_page_length=0):
            raw = deposit.get('allocation_detail') or '[]'
            try:
                ledger = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(ledger, list) or any(not isinstance(entry, dict) for entry in ledger):
                    raise ValueError()
                for entry in ledger:
                    if entry.get('tipo') == 'Cobranza' and entry.get('periodo') == previous.name:
                        protected.update(keys.get(clean_text(entry.get('fila_id')), ()))
            except (ValueError, TypeError):
                protected.update(name for key, names in keys.items() if not key or key in str(raw) for name in names)
    if protected:
        row = changed[sorted(protected)[0]]
        frappe.throw(_('La fila {0} de cobranza tiene aplicaciones, depósitos o destinos vinculados. '
            'Retire esos vínculos por el flujo autorizado antes de cambiar su cliente, crédito, cuota o Fila ID, '
            'o de eliminarla. La conciliación existente se conserva.').format(row.get('idx') or row.get('row_key')))
