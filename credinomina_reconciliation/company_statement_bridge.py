"""Read-only bridge from original postings to dated management balances.

Only linked operations are netted. Unattributed differences remain explicit;
they are never converted into balancing entries or assumed registration tasks.
"""
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import nowdate

from credinomina_reconciliation.company_accounting_statement import load_core_detail, validate_dates
from credinomina_reconciliation.credit_application import journal_allocations
from credinomina_reconciliation.historical_cutoff import load_cutoff, cutoff_message
from credinomina_reconciliation.rounding import money, money_float, decimal_value

BRIDGE = 'Conciliación'
SCOPED_FILTERS = ('from_date', 'to_date', 'source_account', 'source_currency', 'component',
    'status', 'origin_type', 'category', 'reason_type', 'subcategory', 'accounting_status',
    'management_status', 'treatment_status', 'search')


def _total(values):
    values = list(values)
    return None if any(value is None for value in values) else sum((money(value) for value in values), money(0))


def _number(value):
    return None if value is None else money_float(value)


def execute(filters):
    if (any(filters.get(field) for field in SCOPED_FILTERS)
            or str(filters.get('accounting_queue') or '0') in {'1', 'True'}
            or filters.get('statement_scope') not in (None, '', 'Todos')):
        frappe.throw(_('La vista Conciliación compara el alcance completo por empresa y fecha de corte. '
            'Quite los filtros de detalle, cuenta, moneda y sección para no mezclar poblaciones distintas.'))
    scope = dict(employer=filters.get('employer'), cutoff_date=filters.get('cutoff_date') or nowdate())
    validate_dates(scope)
    core = load_core_detail(scope)
    snapshot = load_cutoff(scope, include_bridge=True)
    rows = build_bridge(core, snapshot, scope.get('employer'))
    message = _('Saldo contable + diferencias identificadas + diferencia sin explicar = saldo de gestión. '
        'El saldo de gestión suma aplicaciones pendientes y CxC, y resta saldos a favor y depósitos sin asignar. '
        'Cada importe de diferencia conserva su signo; no representa por sí solo un asiento a registrar. '
        'Verificar contrapartida no significa que falte registro. El redondeo de conversión no requiere asiento. '
        'Pendiente de registro corresponde al estado actual de la partida en la app; Contabilidad debe comprobar el mayor completo. '
        'Los vínculos sin efecto neto se compensan dentro de la explicación y no se vuelven a sumar. ')
    message += '<br>' + cutoff_message(snapshot)
    return get_columns(filters), rows, message


def build_bridge(core, snapshot, employer=None):
    context = snapshot['bridge_context']
    detail = snapshot['detail']  # Signed management values, before presentation.
    companies = {row.get('employer') or '' for row in [*core, *detail]}
    if employer:
        companies = {employer}
    items = {item['name']: item for item in context['items']}
    deposits = {row['name']: row for row in context['deposits'] if row.get('cash_identity_status') != 'Origen vinculado'}
    sources = {row['name']: row for row in context['sources']}
    nodes, parents, ledger = {}, {}, defaultdict(list)

    def node(company, kind, name, source=None):
        company = company or ''
        if company not in companies:
            return None
        key = (company, kind, name)
        if key not in nodes:
            nodes[key] = dict(amount=money(0), source=source or {}, kind=kind, name=name)
            parents[key] = key
        elif source:
            nodes[key]['source'] = source
        return key

    def add(key, value):
        if key is not None:
            nodes[key]['amount'] = _total((nodes[key]['amount'], value))

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def join(left, right):
        if left is not None and right is not None and left[0] == right[0]:
            parents[root(right)] = root(left)

    for line in core:
        if line.get('remittance_allocation'):
            kind, name = 'deposit', line['remittance_allocation']
        elif line.get('complementary_item'):
            kind, name = 'item', line['complementary_item']
        elif line.get('source_row_id'):
            kind, name = 'application', line['source_row_id']
        else:
            kind, name = 'unlinked', line.get('evidence_key') or line['source_document']
        key = node(line.get('employer'), kind, name, line)
        if key is not None:
            ledger[key].append(line)
            add(key, None if line.get('core_balance_usd') is None else -money(line['core_balance_usd']))

    for deposit in deposits.values():
        add(node(deposit.get('employer'), 'deposit', deposit['name'], deposit),
            None if deposit.get('amount_usd') is None else -money(deposit['amount_usd']))
    for source in sources.values():
        add(node(source.get('employer'), 'application', source['name'], source), source.get('bridge_applied_usd'))
    funding = defaultdict(lambda: money(0))
    for entry in journal_allocations(items.values(), set(sources), snapshot['cutoff_date']):
        funding[entry['item']] += money(entry['importe_usd'])
    for item in items.values():
        key = node(item.get('employer'), 'item', item['name'], item)
        if item.get('accounting_source_key') and not item.get('origin_deposit'):
            add(key, item.get('_signed_pending_usd'))
        if (item.get('docstatus') == 1 and item.get('result') == 'Saldo a favor documentado'
                and item.get('category') in {'Saldo a favor del cliente', 'Saldo a favor de la empresa'}):
            add(key, None if item.get('credit_usage_issue') else money(item.get('credit_resolved_usd')) - funding[item['name']])
        for entry in context['distributions'].get(item['name'], []):
            # Use the actual payer: cross-company funding cannot disappear in a net.
            deposit = deposits.get(entry.get('deposit'), {})
            add(node(deposit.get('employer'), 'item', item['name'], item), entry.get('amount_usd'))
    for row in snapshot['receivables']:
        add(node(row.get('employer'), 'item', row['name'], items.get(row['name'])), row.get('receivable_usd'))

    # Explicit links make adjustment/reversal pairs neutral, not separate uses.
    for item in items.values():
        key = node(item.get('employer'), 'item', item['name'], item)
        application = sources.get(item.get('related_application'))
        if application:
            join(key, node(application.get('employer'), 'application', application['name'], application))
        for field in ('_registration_origin', 'receivable_origin'):
            other = items.get(item.get(field))
            if other:
                join(key, node(other.get('employer'), 'item', other['name'], other))
    for offset in context['offsets']:
        left, right = items.get(offset['parent']), items.get(offset.get('counterpart'))
        if left and right:
            join(node(left.get('employer'), 'item', left['name'], left),
                 node(right.get('employer'), 'item', right['name'], right))

    groups = defaultdict(list)
    for key in nodes:
        groups[root(key)].append(key)
    changes = defaultdict(list)
    for key, members in groups.items():
        amount = _total(nodes[member]['amount'] for member in members)
        if amount == 0:
            continue
        row = _explanation(key[0], members, nodes, ledger, context)
        row['statement_amount_usd'] = _number(amount)
        changes[key[0]].append(row)

    output = []
    for company in sorted(companies):
        original = _total(row.get('core_balance_usd') for row in core if (row.get('employer') or '') == company)
        target = _total(row.get('balance_usd') for row in detail if (row.get('employer') or '') == company)
        lines = sorted(changes[company], key=lambda row: (str(row.get('event_date') or ''), row.get('source_document') or ''))
        explained = _total(row['statement_amount_usd'] for row in lines)
        remainder = None if any(value is None for value in (original, target, explained)) else target - original - explained
        running = original
        opening = dict(bridge_concept=_('Saldo contable del core'), bridge_row_type='opening',
            statement_amount_usd=_number(original), bridge_running_usd=_number(original), _is_statement_total=True)
        rows = [opening]
        for line in lines:
            running = _total((running, line['statement_amount_usd']))
            rows.append(dict(line, bridge_running_usd=_number(running)))
        if remainder is None or remainder:
            rows.append(dict(bridge_concept=_('Diferencia sin explicar') if remainder is not None else _('Diferencia no determinable'),
                bridge_row_type='unexplained', statement_amount_usd=_number(remainder), bridge_registration=_('Revisar'),
                observation=_('Los vínculos disponibles no explican toda la diferencia. Revise alcance, efectivo entre empresas, '
                    'orígenes excluidos y conversiones. Este importe no es una partida ni un asiento propuesto.')))
        rows.append(dict(bridge_concept=_('Saldo de gestión'), bridge_row_type='closing',
            statement_amount_usd=_number(target), bridge_running_usd=_number(target), _is_statement_total=True,
            bridge_registration=_('Diferencia explicada') if remainder == 0 else _('Revisión pendiente'),
            unexplained_usd=_number(remainder)))
        output.extend(dict(row, employer=company or None, usd_currency='USD', nio_currency='NIO',
            cutoff_date=snapshot['cutoff_date']) for row in rows)
    return output


def _explanation(company, members, nodes, ledger, context):
    item_keys = [key for key in members if key[1] == 'item']
    primary = (item_keys or members)[0]
    source = nodes[primary]['source']
    kind, name = primary[1:]
    documents = sorted({nodes[key]['name'] for key in members if key[1] in {'item', 'deposit'}})
    uses = [entry for key in item_keys for entry in context['distributions'].get(key[2], [])]
    row = dict(employer=company, bridge_row_type='difference',
        source_document=name if kind in {'item', 'deposit'} else source.get('parent') or source.get('source_document'),
        source_doctype={'item': 'CN Complementary Item', 'deposit': 'CN Remittance Allocation'}.get(kind, 'CN Accounting Import'),
        event_date=source.get('deposit_date') or source.get('posting_date') or source.get('event_date'),
        related_documents=', '.join(documents), related_deposits=', '.join(sorted({entry['deposit'] for entry in uses})),
        accounting_status=source.get('accounting_status') or '', client_name=source.get('client_name') or '',
        reference=source.get('deposit_voucher') or source.get('source_voucher') or source.get('voucher') or source.get('reference') or '',
        observation=source.get('description') or source.get('source_description') or '',
        bridge_registration=_('Verificar vínculo'), bridge_concept=_('Diferencia entre origen y gestión'))
    if kind == 'deposit':
        lines = ledger.get(primary, [])
        original = _total(line.get('core_balance_usd') for line in lines)
        nio = _total(line.get('core_balance_nio') for line in lines)
        row.update(core_amount_usd=_number(-original) if original is not None else None,
            management_amount_usd=source.get('amount_usd'),
            bridge_concept=_('Depósito bancario distinto del crédito contable'),
            bridge_registration=_('Verificar contrapartida'), related_deposits=name,
            observation=_('Comparar el comprobante completo: el importe bancario puede incluir otras cuentas o clientes. '
                'La diferencia no demuestra que falte un asiento.'))
        if not source.get('deposit_date'):
            row.update(bridge_concept=_('Origen contable sin depósito efectivo al corte'),
                bridge_registration=_('Verificar fecha / estado'),
                observation=_('El movimiento existe en el core, pero el depósito no participa en la gestión al corte. '
                    'Revise su fecha efectiva, confirmación o cancelación.'))
        elif not lines:
            row.update(bridge_concept=_('Depósito sin origen contable al corte'),
                observation=_('El depósito participa en gestión, pero no tiene un movimiento contable importado dentro del corte. '
                    'Revise el origen, su fecha y el comprobante completo.'))
        rate = decimal_value(source.get('fx_rate'))
        rounding = (bool(lines) and source.get('deposit_currency') == 'NIO' and source.get('deposit_amount') is not None
            and nio is not None and -nio == money(source['deposit_amount']) and rate > 0
            and all(line.get('source_currency') == 'NIO' and decimal_value(line.get('fx_rate')) == rate for line in lines)
            and source.get('amount_usd') is not None and money(source['amount_usd']) == money(-nio / rate))
        if rounding:
            row.update(bridge_concept=_('Redondeo de conversión por líneas'), bridge_registration=_('No requiere asiento'),
                observation=_('El importe bancario y el crédito contable coinciden en córdobas. La diferencia surge al '
                    'sumar conversiones redondeadas por línea frente a convertir el total.'))
    elif item_keys:
        involved = [nodes[key]['source'] for key in item_keys]
        imported = any(item.get('accounting_source_key') for item in involved)
        statuses = {item.get('accounting_status') or '' for item in involved}
        row['accounting_status'] = ', '.join(sorted(status for status in statuses if status))
        if not imported and statuses & {'Pendiente de registro', 'Asiento informado'}:
            row['bridge_registration'] = _('Pendiente de registro') if 'Pendiente de registro' in statuses else _('Asiento informado; verificar')
        elif statuses == {'No requiere registro'}:
            row['bridge_registration'] = _('No requiere registro')
        elif imported or statuses == {'Registrada'}:
            row['bridge_registration'] = _('Registro existente; verificar vínculo')
        if uses:
            row['bridge_concept'] = _('Ajuste utilizado en conciliación')
        elif any(item.get('credit_resolved_usd') for item in involved):
            row['bridge_concept'] = _('Gestión del saldo a favor y su contrapartida')
        if len(members) > 1:
            row['observation'] += ' ' + _('Efecto neto de los documentos vinculados; incluye compensaciones, aplicaciones y reversiones al corte.')
    else:
        row['bridge_concept'] = _('Aplicación u origen con diferente alcance en gestión')
    return row


def get_columns(filters):
    compact = str(filters.get('compact_view', 1)) in {'1', 'True'}
    columns = [dict(fieldname='employer', label=_('Empresa'), fieldtype='Link', options='CN Employer', width=150),
        dict(fieldname='bridge_concept', label=_('Concepto de conciliación'), fieldtype='Data', width=290),
        dict(fieldname='statement_amount_usd', label=_('Importe US$'), fieldtype='Currency', options='usd_currency', width=140, precision=2),
        dict(fieldname='source_document', label=_('Documento de origen'), fieldtype='Dynamic Link', options='source_doctype', width=200),
        dict(fieldname='bridge_registration', label=_('Acción / registro'), fieldtype='Data', width=220),
        dict(fieldname='event_date', label=_('Fecha de origen'), fieldtype='Date', width=110),
        dict(fieldname='related_deposits', label=_('Depósitos relacionados'), fieldtype='Data', width=200),
        dict(fieldname='observation', label=_('Explicación'), fieldtype='Data', width=420),
        dict(fieldname='source_doctype', label=_('Tipo de origen'), fieldtype='Data', hidden=1)]
    for field, label, kind in (
        ('bridge_running_usd', 'Saldo después del movimiento US$', 'Currency'),
        ('core_amount_usd', 'Crédito contable original US$', 'Currency'),
        ('management_amount_usd', 'Importe bancario US$', 'Currency'),
        ('accounting_status', 'Estado de registro en la app', 'Data'),
        ('related_documents', 'Documentos vinculados', 'Data'),
        ('client_name', 'Cliente', 'Data'), ('reference', 'Referencia / asiento', 'Data')):
        columns.append(dict(fieldname=field, label=_(label), fieldtype=kind, width=200, hidden=int(compact),
            **(dict(options='usd_currency', precision=2) if kind == 'Currency' else {})))
    columns.append(dict(fieldname='cutoff_date', label=_('Corte efectivo'), fieldtype='Date', width=110))
    return columns
