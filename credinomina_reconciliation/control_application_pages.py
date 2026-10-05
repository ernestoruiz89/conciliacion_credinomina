"""Database-paged unassigned applications, restricted to readable imports.

Counting the complete population is separate from loading its 100 display rows.
The historic-routing and cent-rounded net amount mirror the control builder.
"""
import frappe

from credinomina_reconciliation.historical import OPERATIVE_START
from credinomina_reconciliation.report_records import records

FIELDS = (
    'name', 'parent', 'source_row', 'event_date', 'reference', 'client_name',
    'loan_number', 'client_number', 'accounting_entry', 'receipt', 'amount',
    'amount_usd', 'currency', 'match_reason', 'application_adjustment_usd',
    'net_applied_usd', 'processing_route',
)


def historical_page(year=None, employer=None, start=0):
    if not frappe.has_permission('CN Accounting Import', 'read'):
        return {'rows': [], 'count': 0}
    parents = [row.name for row in records('CN Accounting Import',
        filters={'status': ['in', ['Importado', 'Importado con excepciones']],
                 **({'employer': employer} if employer else {})}, fields=['name'])]
    if not parents:
        return {'rows': [], 'count': 0}
    conditions = [
        'p.name in %(parents)s', "s.parenttype='CN Accounting Import'",
        "s.parentfield='rows'", "s.event_type='Aplicacion'", 's.effective=1',
        "coalesce(s.historical_period, '')=''", "coalesce(s.collection_period, '')=''",
        'round(coalesce(s.amount, 0), 2) - round(coalesce(s.application_adjustment_usd, 0), 2) > 0',
        """(s.processing_route='Historica' or s.event_date < %(operative_start)s
            or (coalesce(s.processing_route, '') != 'Operativa'
                and (p.historical_backfill=1 or coalesce(p.historical_period, '') != '')))""",
    ]
    values = {'parents': tuple(parents), 'operative_start': OPERATIVE_START,
              'start': max(int(start), 0)}
    if year is not None:
        conditions.append('s.event_date between %(date_from)s and %(date_to)s')
        values.update(date_from=f'{year}-01-01', date_to=f'{year}-12-31')
    scope = ('from `tabCN Source Row` s join `tabCN Accounting Import` p on p.name=s.parent '
             'where ' + ' and '.join(conditions))
    count = frappe.db.sql('select count(*) ' + scope, values)[0][0]
    if values['start'] >= count:
        return {'rows': [], 'count': count}
    columns = ', '.join(f's.`{field}`' for field in FIELDS)
    rows = frappe.db.sql(f'select {columns}, p.employer {scope} '
        'order by s.event_date asc, s.idx asc, s.name asc limit 100 offset %(start)s',
        values, as_dict=True)
    return {'rows': rows, 'count': count}
