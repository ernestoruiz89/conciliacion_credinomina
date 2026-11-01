"""Read-only, non-overlapping portions of confirmed undistributed deposits."""
from collections import defaultdict
import frappe

from credinomina_reconciliation.report_records import records, child_records
from credinomina_reconciliation.rounding import money, money_float, sum_money


def build(deposits, details):
    grouped = defaultdict(list)
    for row in details:
        if row.get('row_kind') != 'Diferencia documentada':
            grouped[row['parent']].append(row)
    output = []
    for deposit in deposits:
        if deposit.get('cash_identity_status') in {'Origen vinculado', 'Revisar identidad'}:
            continue
        remaining = money(deposit.get('amount_usd')) - money(deposit.get('allocated_usd')) - money(deposit.get('justified_surplus_usd'))
        if remaining <= 0:
            continue
        rows = grouped[deposit['name']]
        # A detail is attributable only when the complete set of positive
        # remainders fits in the actual unclassified cash pool.
        pending = [row for row in rows if money(row.get('pending_usd')) > 0]
        consistent = (money(deposit.get('unclassified_usd')) == remaining
                      and sum_money(row.get('amount_usd') for row in rows) <= money(deposit.get('amount_usd'))
                      and all(0 <= money(row.get('pending_usd')) <= money(row.get('amount_usd')) for row in rows)
                      and sum_money(row.get('pending_usd') for row in pending) <= remaining)
        base = dict(position_type='Depósito sin asignar', event_date=deposit.get('deposit_date'),
                    source_doctype='CN Remittance Allocation', source_document=deposit['name'],
                    employer=deposit.get('employer'), operational_status='Pendiente' if consistent else 'Revisar distribución', is_open=True,
                    usd_currency='USD')
        attributed = money(0)
        if consistent:
            for row in pending:
                if not any(row.get(field) for field in ('client_number', 'national_id', 'loan_number', 'client_name')):
                    continue
                amount = money(row['pending_usd'])
                attributed += amount
                output.append(dict(base, client_name=row.get('client_name'), client_number=row.get('client_number'),
                    national_id=row.get('national_id'), loan_number=row.get('loan_number'),
                    deposit_pending_usd=money_float(amount), source_rows=row['name'],
                    observation='Importe pendiente del detalle del depósito.'))
        remainder = remaining - attributed
        if remainder > 0:
            output.append(dict(base, client_name='Sin cliente individual', deposit_pending_usd=money_float(remainder),
                observation=('Efectivo pendiente sin atribución individual.' if consistent else
                             'Revise la distribución: el detalle no permite atribuir el efectivo pendiente por cliente.')))
    return output


def load(employer=None):
    if not frappe.has_permission('CN Remittance Allocation', 'read'):
        return []
    deposits = list(records('CN Remittance Allocation', filters={'docstatus': 1, **({'employer': employer} if employer else {})},
        fields=['name', 'employer', 'deposit_date', 'amount_usd', 'allocated_usd', 'justified_surplus_usd',
                'unclassified_usd', 'cash_identity_status']))
    parents = {row['name']: row for row in deposits}
    details = child_records('CN Remittance Detail', parents, 'CN Remittance Allocation', 'detail_rows',
        fields=['name', 'parent', 'row_kind', 'client_name', 'client_number', 'national_id', 'loan_number', 'amount_usd', 'pending_usd'])
    return build(deposits, details)
