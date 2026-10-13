"""Bounded, owner-scoped review pages from the frozen analysis, not a new import."""
import json
import unicodedata

import frappe
from frappe import _
from frappe.utils import cint

from credinomina_reconciliation.accounting_batch_store import BLOCK, insert_internal, dumps

PAGE_SIZE = 50
CHUNK_SIZE = 250


def unidentified_rows(plan):
    def unknown(company):
        return not company or company == 'NO IDENTIFICADA'
    for group in plan['groups']:
        if unknown(group['employer']):
            for row in group['rows']:
                yield _record(row, 'Aplicación')
    for field, label in [('complementary', 'Partida complementaria'), ('deposits', 'Depósito')]:
        for row in plan.get(field, []):
            if unknown(row.get('resolved_employer')):
                yield _record(row, label)


def _record(row, kind):
    return {'row': row['source_row'], 'kind': kind, 'event_date': row.get('event_date') or row.get('source_date'),
            'client_name': row.get('client_name') or '', 'loan_number': row.get('loan_number') or '',
            'employer_text': row.get('employer_text') or '', 'voucher': row.get('voucher') or '',
            'description': row.get('source_description') or row.get('description') or '',
            'total_usd': row.get('amount_usd'), 'reference': row.get('bank_deposit_reference') or '',
            'reason': row.get('portfolio_validation_status') or row.get('classification_reason') or ''}


def save_preview(token, plan):
    def save(index, chunk):
        name, payload = f'{token}-preview-{index}', dumps({'review_rows': chunk})
        previous = frappe.db.get_value(BLOCK, name, 'payload')
        if previous:
            if previous != payload:
                frappe.throw(_('La evidencia del análisis cambió. Genere una nueva vista previa conservando sus selecciones.'))
            return  # Retried delivery after commit: never duplicate preview rows.
        insert_internal(BLOCK, name, batch=token, block_index=-index, payload=payload)
    chunk, index = [], 0
    for row in unidentified_rows(plan):
        chunk.append(row)
        if len(chunk) == CHUNK_SIZE:
            index += 1
            save(index, chunk)
            chunk = []
    if chunk:
        index += 1
        save(index, chunk)
    return index


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').casefold()) if not unicodedata.combining(c))


def review_page(chunks, choices, *, start=0, search='', kind=''):
    """Scan bounded blobs, retaining only one page; all choices remain in the batch."""
    start = max(cint(start), 0)
    terms, total, output = normalized(search).split(), 0, []
    for chunk in chunks:
        for row in chunk:
            company = choices.get(str(row['row'])) or 'NO IDENTIFICADA'
            text = normalized(' '.join(str(value or '') for value in [*row.values(), company]))
            if (kind and row['kind'] != kind) or not all(term in text for term in terms):
                continue
            if start <= total < start + PAGE_SIZE:
                output.append(dict(row, employer=company))
            total += 1
    return {'rows': output, 'count': total, 'start': start, 'page_size': PAGE_SIZE}


@frappe.whitelist()
def get_unidentified_page(token, start=0, search='', kind=''):
    from credinomina_reconciliation.bulk_accounting_import import _state
    state = _state(token)  # Create/write permission AND exact batch owner.
    if not state.get('preview_chunks'):
        if state.get('summary', {}).get('unidentified_count'):
            frappe.throw(_('Genere un nuevo análisis para habilitar las páginas de esta vista previa. Se conservarán sus selecciones.'))
        return review_page([], {}, start=start)
    if state['phase'] != 'preview' or state['status'] not in {'Vista previa', 'Error'}:
        frappe.throw(_('La carga ya está en proceso; consulte sus documentos registrados.'))
    def chunks():
        for index in range(1, state['preview_chunks'] + 1):
            payload = frappe.db.get_value(BLOCK, f'{token}-preview-{index}', 'payload')
            if not payload:
                frappe.throw(_('La evidencia de esta página no está disponible. Genere un nuevo análisis.'))
            yield json.loads(payload)['review_rows']
    choices = state.get('draft_assignments', state['options'].get('employer_assignments', {}))
    return review_page(chunks(), choices, start=start, search=search, kind=kind)
