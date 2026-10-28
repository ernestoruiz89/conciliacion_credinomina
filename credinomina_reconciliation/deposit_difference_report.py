"""Informational accounting work queue; never a second financial balance."""
import frappe
from collections import Counter
from frappe.utils import getdate, nowdate

from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float
from credinomina_reconciliation.company_statement import FIELDS
from credinomina_reconciliation.company_accounting_statement import CORE_FIELDS, MANAGEMENT


def project(deposits, details, cutoff_date=None, from_date=None, to_date=None):
    parents = {d['name']: d for d in deposits if d.get('docstatus') == 1
               and d.get('cash_identity_status') != 'Origen vinculado'}
    historical = bool(cutoff_date and getdate(cutoff_date) < getdate(nowdate()))
    result = []
    counts = Counter(detail.get('parent') for detail in details if detail.get('row_kind') == 'Diferencia documentada')
    seen = set()
    for detail in details:
        deposit = parents.get(detail.get('parent'))
        if not deposit or detail.get('row_kind') != 'Diferencia documentada':
            continue
        if deposit['name'] in seen:
            continue
        seen.add(deposit['name'])
        if cutoff_date and getdate(deposit['deposit_date']) > getdate(cutoff_date):
            continue
        if from_date and getdate(deposit['deposit_date']) < getdate(from_date):
            continue
        if to_date and getdate(deposit['deposit_date']) > getdate(to_date):
            continue
        # Current state can prove a settled core link, but cannot establish
        # when the link was made or removed in a previous historical cutoff.
        if (not historical and counts[deposit['name']] == 1 and
                not money(detail.get('difference_core_pending_usd')) and detail.get('match_status') != 'Revisar'):
            continue
        if counts[deposit['name']] > 1:
            status, amount = 'Revisar documentación', None
            observation = 'Hay más de una fila de diferencia en el depósito; no se determina un importe hasta revisarlas.'
        elif historical:
            status, amount = 'Estado no determinable al corte', None
            observation = 'La fecha de vinculación al core no tiene historial suficiente para reconstruir este corte.'
        elif detail.get('match_status') == 'Revisar':
            status, amount = 'Revisar documentación', None
            observation = detail.get('match_reason') or 'Revise la documentación de la diferencia.'
        elif detail.get('difference_core_item'):
            status, amount = 'Core vinculado; pendiente de conciliación', 0.0
            observation = 'El movimiento del core está vinculado; falta conciliarlo. No se suma otro importe al saldo.'
        else:
            status = 'Pendiente de registro en core'
            signed = money(detail.get('difference_amount_usd'))
            amount = money_float(abs(money(detail.get('difference_core_pending_usd'))) * (-1 if signed < 0 else 1))
            observation = 'Control informativo de la diferencia del depósito; no suma otro importe al saldo de gestión.'
        row = dict(employer=deposit.get('employer'), usd_currency='USD', nio_currency='NIO',
            event_date=deposit.get('deposit_date'), position_type='Diferencia de depósito',
            source_doctype='CN Remittance Allocation', source_document=deposit['name'],
            client_name=deposit.get('employer'), origin_type='Diferencia de depósito',
            reference=deposit.get('deposit_reference'), category='Diferencia de depósito',
            difference_reason=detail.get('difference_reason'), accounting_status=(
                'Pendiente de registro' if status == 'Pendiente de registro en core' else
                'Importada del core' if status == 'Core vinculado; pendiente de conciliación' else ''),
            status=status, accounting_pending=status == 'Pendiente de registro en core', observation=observation,
            pending_core_difference_usd=amount, balance_usd=0.0, management_amount_usd=0.0,
            statement_scope=MANAGEMENT, _component=None,
            **{field: 0.0 for field in (*FIELDS, *CORE_FIELDS)})
        result.append(row)
    return result


def load(filters):
    if not frappe.has_permission('CN Remittance Allocation', 'read'):
        frappe.throw('Necesita permiso de lectura de depósitos.', frappe.PermissionError)
    scope = {'docstatus': 1}
    if filters.get('employer'):
        scope['employer'] = filters['employer']
    start = filters.get('from_date')
    ends = [value for value in (filters.get('to_date'), filters.get('cutoff_date')) if value]
    end = min(ends, key=getdate) if ends else None
    if start and end:
        scope['deposit_date'] = ['between', [start, end]]
    elif start:
        scope['deposit_date'] = ['>=', start]
    elif end:
        scope['deposit_date'] = ['<=', end]
    deposits = list(records('CN Remittance Allocation', filters=scope,
        fields=['name', 'employer', 'docstatus', 'deposit_date', 'deposit_reference', 'cash_identity_status']))
    details = list(child_records('CN Remittance Detail', [d['name'] for d in deposits],
        'CN Remittance Allocation', 'detail_rows', filters={'row_kind': 'Diferencia documentada'},
        fields=['name', 'parent', 'row_kind', 'difference_amount_usd', 'difference_core_pending_usd',
                'difference_core_item', 'difference_reason', 'match_status', 'match_reason']))
    return project(deposits, details, filters.get('cutoff_date'), filters.get('from_date'), filters.get('to_date'))
