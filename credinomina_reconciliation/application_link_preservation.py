"""Reserve persisted payroll matches before matching additional applications."""
import json

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money, money_float, sum_money
from credinomina_reconciliation.reconciliation import net_application_amount


def preserve(rows, originals, collections, *, exclude_import=None):
    previous = {row.get('name'): row for row in originals}
    targets = {row.name: row for row in collections}
    fixed = []
    for row in rows:
        old = previous.get(row.name) or {}
        if ((exclude_import and row.get('_source_import') == exclude_import) or row.get('event_type') != 'Aplicacion'
                or not old.get('effective') or old.get('match_status') not in {'Conciliado', 'Enlace provisional'}):
            continue
        try:
            links = json.loads(old.get('application_allocation_detail') or '[]')
        except (ValueError, TypeError):
            frappe.throw(_('La aplicación {0} tiene vínculos guardados ilegibles. Revise su conciliación antes de continuar.').format(row.name))
        if not links and old.get('collection_row_id'):
            links = [dict(collection_row_id=old['collection_row_id'], amount_usd=net_application_amount(old))]
        if not links:
            continue  # Direct application-level cash does not require payroll.
        identity_changed = any(old.get(field) and row.get(field) != old.get(field)
                               for field in ('client', 'client_number', 'national_id', 'loan_number'))
        if row.get('currency') != 'USD' or identity_changed:
            frappe.throw(_('La aplicación {0} cambió de moneda o identidad y conserva vínculos confirmados. Revise explícitamente su conciliación antes de continuar.').format(row.name))
        valid = (isinstance(links, list) and all(isinstance(link, dict) and link.get('collection_row_id') in targets
                 and (not link.get('period') or link['period'] == targets[link['collection_row_id']].parent)
                 and money(link.get('amount_usd')) >= 0 for link in links))
        if not valid or not row.get('effective'):
            frappe.throw(_('La aplicación {0} ya tiene una conciliación vinculada que no puede retirarse automáticamente. Revise sus destinos antes de continuar.').format(row.name))
        net = money(net_application_amount(row))
        if sum_money(link.get('amount_usd') for link in links) != net:
            if len(links) != 1 or net < 0:
                frappe.throw(_('El importe de la aplicación {0} cambió y su distribución entre cobranzas requiere revisión explícita; se conservaron los vínculos existentes.').format(row.name))
            links = [dict(links[0], amount_usd=money_float(net))]
            encoded = json.dumps(links, ensure_ascii=False)
        else:
            encoded = old.get('application_allocation_detail') or json.dumps(links, ensure_ascii=False)
        row.match_status = old['match_status']
        row.match_reason = old.get('match_reason') or ''
        row.collection_period = old.get('collection_period') or targets[links[0]['collection_row_id']].parent
        row.collection_row_id = old.get('collection_row_id') or ''
        row.application_allocation_detail = encoded
        fixed.append(row)
    return fixed
