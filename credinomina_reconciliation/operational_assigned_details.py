"""Read-only drilldown of the statement's assigned coverage, at the same cutoff."""
from collections import defaultdict

import frappe
from frappe import _

from credinomina_reconciliation.application_deposit_links import _entries
from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.row_link_details import Links


def _source_names(value):
    return set(filter(None, (name.strip() for name in str(value or '').split(','))))


def _current_evidence(row):
    names = _source_names(row.get('source_rows'))
    parents = frappe.get_all('CN Source Row', filters={'name': ['in', sorted(names)],
        'parenttype': 'CN Accounting Import', 'parentfield': 'rows'}, pluck='parent')
    readable = [item.name for item in records('CN Accounting Import', filters={'name': ['in', parents]}, fields=['name'])]
    sources = list(child_records('CN Source Row', readable, 'CN Accounting Import', 'rows',
        filters={'name': ['in', sorted(names)]}, fields=['name', 'parent', 'historical_period',
            'amount_usd', 'application_adjustment_usd', 'net_applied_usd', 'collection_row_id', 'application_allocation_detail']))
    collection = None
    if row.get('collection_row_id'):
        periods = [item.name for item in records('CN Reconciliation Period', filters={'name': row.get('period')}, fields=['name'])]
        collection = next(iter(child_records('CN Collection Row', periods, 'CN Reconciliation Period', 'collection_rows',
            filters={'name': row['collection_row_id']}, fields=['name', 'parent', 'row_key', 'applied_usd'])), None)
    needles = names | ({collection['row_key']} if collection and collection.get('row_key') else set())
    deposits = list(records('CN Remittance Allocation', filters={'docstatus': 1},
        or_filters=[['allocation_detail', 'like', '%' + value + '%'] for value in sorted(needles)],
        fields=['name', 'deposit_date', 'deposit_reference', 'docstatus', 'cash_identity_status', 'consolidated_deposit',
            'allocation_detail', 'allocated_usd'])) if needles and frappe.has_permission('CN Remittance Allocation', 'read') else []
    from credinomina_reconciliation.application_complementary_links import load_links
    item_links = load_links(sources, include_amounts=True)
    movements = {entry['movimiento'] for deposit in deposits for entry in _entries(deposit.get('allocation_detail')) if entry.get('movimiento')}
    items = list(records('CN Complementary Item', filters={'name': ['in', sorted(movements)], 'docstatus': 1},
        fields=['name', 'docstatus', 'category', 'status', 'claim_id', 'signed_amount_usd'])) if (
            movements and collection and frappe.has_permission('CN Complementary Item', 'read')) else []
    _collection_movements(row, collection, items, item_links)
    return sources, collection, deposits, item_links


def _collection_movements(row, collection, items, item_links):
    # A tolerance belongs to the collection as a whole unless an individual
    # source is recorded. Do not spread it across partially selected members.
    names = sorted(_source_names(row.get('source_rows')))
    if not collection or not names or money(row.get('applied_usd')) != money(collection.get('applied_usd')):
        return
    present = {link['document'] for links in item_links.values() for link in links}
    for item in items:
        if (item.get('docstatus') == 1 and item.get('category') == 'Diferencia por tolerancia'
                and item.get('status') == 'Vigente' and item.get('claim_id') == 'C:' + collection['name']
                and item['name'] not in present):
            item_links.setdefault(names[0], []).append(dict(document=item['name'], category=item['category'],
                amount_usd=money_float(-money(item.get('signed_amount_usd')))))
            present.add(item['name'])


def _cutoff_item_links(items, sources, cutoff):
    from credinomina_reconciliation.application_adjustment_links import links
    from credinomina_reconciliation.credit_application import journal_allocations
    from credinomina_reconciliation.reclassification_distribution import application_links
    names = {source['name'] for source in sources}
    result = defaultdict(list)
    by_name = {item['name']: item for item in items}

    def add(name, item, amount):
        if name in names:
            result[name].append(dict(document=item['name'], category=item.get('category'), amount_usd=money_float(amount)))

    for item in items:
        if item.get('docstatus') != 1:
            continue
        if item.get('category') == 'Ajuste de aplicación':
            for link in links(item):
                add(link.related_application, item, link.application_adjustment_usd)
        if item.get('category') == 'Diferencia por tolerancia' and item.get('status') == 'Vigente':
            add(item.get('application_source_row'), item, -money(item.get('signed_amount_usd')))
    for link in application_links(items, names, cutoff=cutoff):
        # Reclassification links are already included in applied net.
        add(link.related_application, dict(name=link.name, category='Compensación entre partidas'), link.application_adjustment_usd)
    for entry in journal_allocations(items, names, cutoff):
        add(entry['application_id'], by_name[entry['item']], entry['importe_usd'])
    return result


def build_details(row, sources, collection, deposits, item_links):
    names = _source_names(row.get('source_rows'))
    movement_names = {link['document'] for name in names for link in item_links.get(name, [])
                      if link.get('category') == 'Diferencia por tolerancia'}
    data, adjustments = Links(), Links()
    for deposit in deposits:
        if deposit.get('docstatus') != 1 or deposit.get('cash_identity_status') == 'Origen vinculado' or deposit.get('consolidated_deposit'):
            continue
        journal = _entries(deposit.get('allocation_detail'))
        chosen, shared = [], False
        for entry in journal:
            amount = money(0)
            if entry.get('tipo') == 'Aplicacion historica' and entry.get('aplicacion_id') in names:
                # An operational row can be only one portion of an application.
                if not collection and (not row.get('period') or entry.get('periodo') == row['period']):
                    amount = money(entry.get('importe_usd'))
            elif entry.get('tipo') == 'Movimiento de conciliación' and entry.get('movimiento') in movement_names:
                amount = money(entry.get('importe_usd'))
            elif (collection and entry.get('tipo') == 'Cobranza'
                    and (entry.get('periodo'), entry.get('fila_id')) == (collection['parent'], collection['row_key'])):
                parts = _entries(entry.get('aplicaciones_fifo'))
                if not parts and money(collection.get('applied_usd')) != money(row.get('applied_usd')):
                    shared = True
                    continue
                amount = (sum_money(part.get('amount_usd') for part in parts if part.get('application_id') in names)
                          if parts else money(entry.get('importe_usd')))
            if amount:
                chosen.append((entry, amount))
        if shared:
            data.add('CN Remittance Allocation', deposit['name'], None, 'Depósito', 'Cobertura compartida')
            data.notes.add('La cobertura de esta cuota no tiene reparto individual entre todas sus aplicaciones. No se asigna el total compartido a una sola fila.')
            continue
        if not chosen:
            continue
        coverage = sum_money(amount for entry, amount in chosen)
        offsets = [entry for entry in journal if money(entry.get('importe_usd')) < 0
                   and entry.get('tipo') in {'Partida complementaria', 'Diferencia documentada de empresa'}]
        whole = coverage == sum_money(entry.get('importe_usd') for entry in journal if money(entry.get('importe_usd')) > 0)
        if offsets and not whole:
            data.add('CN Remittance Allocation', deposit['name'], coverage,
                     'Depósito y complementarias (cobertura conjunta)', 'Conciliado')
            data.notes.add('El depósito comparte complementarias con otras aplicaciones. Se muestra su cobertura vinculada; no se inventa un reparto del efectivo ni de las complementarias.')
            for entry in offsets:
                if entry.get('partida'):
                    # Informational links, excluded from the coverage sum.
                    adjustments.add('CN Complementary Item', entry['partida'], None, 'Complementaria compartida; incluida en cobertura', 'Conciliado')
        else:
            offset = sum_money(entry.get('importe_usd') for entry in offsets)
            data.add('CN Remittance Allocation', deposit['name'], money_float(coverage + offset), 'Depósito', 'Conciliado')
            for entry in offsets:
                data.add('CN Complementary Item', entry.get('partida') or '', -money(entry['importe_usd']),
                         'Complementaria aplicada' if entry.get('partida') else 'Diferencia de empresa', 'Conciliado')
    for name in names:
        for link in item_links.get(name, []):
            is_adjustment = link['category'] in {'Ajuste de aplicación', 'Compensación entre partidas'}
            (adjustments if is_adjustment else data).add('CN Complementary Item', link['document'], link['amount_usd'],
                link['category'], 'Descontado del aplicado neto' if is_adjustment else 'Confirmado')
    result = data.result()
    result['adjustments'] = adjustments.result()['rows']
    result['notes'].extend(sorted(adjustments.notes))
    result['assigned_usd'] = row.get('assigned_usd')
    if result['total_usd'] is None or money(result['total_usd']) != money(row.get('assigned_usd')):
        result['notes'].append('El desglose identificable no explica por completo el importe asignado. Puede existir cobertura compartida o evidencia no disponible; revise los vínculos y sus permisos.')
    return result


@frappe.whitelist()
def get_details(source_rows, period=None, collection_row_id=None, filters=None):
    from frappe.desk.query_report import get_report_doc
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo.estado_de_cuenta_operativo import load_report_rows
    get_report_doc('Estado de Cuenta Operativo')
    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters or {}
    filters, columns, rows, snapshot = load_report_rows(filters)
    matches = [row for row in rows if row.get('position_type') == 'Aplicación'
        and _source_names(row.get('source_rows')) == _source_names(source_rows)
        and (row.get('period') or '') == (period or '')
        and (row.get('collection_row_id') or '') == (collection_row_id or '')]
    if len(matches) != 1:
        frappe.throw(_('La fila cambió o no está disponible. Actualice el reporte y vuelva a consultar el importe asignado.'))
    row = matches[0]
    if snapshot:
        context = snapshot['position_context']
        sources = [source for source in context['sources'] if source['name'] in _source_names(source_rows)]
        collection = context['collections'].get(row.get('collection_row_id'))
        item_links = _cutoff_item_links(context['items'], sources, snapshot['cutoff_date'])
        _collection_movements(row, collection, context['items'], item_links)
        evidence = sources, collection, context['deposits'], item_links
    else:
        evidence = _current_evidence(row)
    result = build_details(row, *evidence)
    return dict(result, client_name=row.get('client_name'), cutoff_date=filters.get('cutoff_date'))
