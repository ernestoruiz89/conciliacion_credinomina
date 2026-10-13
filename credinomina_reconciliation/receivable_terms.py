"""Financial deadlines are frozen separately from management commitments."""
import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.complementary_subcategories import ADJUSTMENT, RECEIVABLE


def is_receivable(doc):
    return doc.get('category') == ADJUSTMENT and doc.get('subcategory_effect') == RECEIVABLE


@frappe.whitelist()
def preview_deadline(period):
    document = frappe.get_doc('CN Reconciliation Period', period)
    document.check_permission('read')
    due, origin = inherited_deadline({'period': period, 'employer': document.employer})
    return {'due_date': str(due) if due else None, 'origin': origin or 'Vencimiento pendiente de verificar'}


def inherited_deadline(doc):
    """Use recorded application deadlines, never a task's commitment date."""
    period = doc.get('period')
    if not period or not frappe.has_permission('CN Accounting Import', 'read'):
        return None, ''
    parent_filters = {'employer': doc.get('employer'), 'status': ['in', ['Importado', 'Importado con excepciones']], 'docstatus': ['!=', 2]}
    parents = frappe.get_list('CN Accounting Import', filters=parent_filters, pluck='name', limit_page_length=0)
    if len(parents) != frappe.db.count('CN Accounting Import', parent_filters):
        return None, 'Evidencia restringida: vencimiento pendiente de verificar'
    claims = set(frappe.get_all('CN Collection Row', filters={'parent': period,
        'parenttype': 'CN Reconciliation Period', 'parentfield': 'collection_rows'}, pluck='name'))
    # An application can have both a direct period and collection links. Include
    # each physical row once, including links to a second operative period.
    from credinomina_reconciliation.accounting_client_summary import entries
    from credinomina_reconciliation.report_records import child_records
    rows = []
    for row in child_records('CN Source Row', parents, 'CN Accounting Import', 'rows',
            filters={'event_type': 'Aplicacion', 'effective': 1, 'docstatus': ['!=', 2]},
            fields=['payment_due_date', 'payment_term_origin', 'historical_period', 'collection_period',
                    'collection_row_id', 'application_allocation_detail']):
        if (period in {row.historical_period, row.collection_period} or row.collection_row_id in claims
                or any(link.get('collection_row_id') in claims for link in entries(row.application_allocation_detail))):
            rows.append(row)
    if not rows or any(not row.payment_due_date for row in rows):
        return None, ''
    due = min(getdate(row.payment_due_date) for row in rows)
    origins = sorted({row.payment_term_origin or 'Plazo registrado' for row in rows})
    return due, 'Aplicaciones del período · ' + '; '.join(origins)


def validate_terms(doc, previous=None):
    if not is_receivable(doc):
        return
    if previous and previous.get('docstatus') == 1 and previous.get('receivable_due_date'):
        if not doc.get('receivable_due_date') or getdate(doc.receivable_due_date) != getdate(previous.receivable_due_date):
            frappe.throw(_('El vencimiento de una CxC confirmada no cambia al reprogramar su gestión.'))
        doc.receivable_due_date = previous.receivable_due_date
        doc.receivable_due_origin = previous.get('receivable_due_origin')
    else:
        due, origin = inherited_deadline(doc)
        if due:
            doc.receivable_due_date, doc.receivable_due_origin = due, origin
        elif doc.get('receivable_due_date'):
            doc.receivable_due_date = getdate(doc.receivable_due_date)
            doc.receivable_due_origin = 'Vencimiento indicado al registrar la CxC'
        else:
            doc.receivable_due_origin = 'Vencimiento pendiente de verificar'
    missing = not all(doc.get(field) for field in ('credit_assigned_to', 'credit_commitment_date', 'receivable_due_date'))
    doc.receivable_setup_pending = int(missing)
    legacy_incomplete = previous and previous.get('docstatus') == 1 and previous.get('receivable_setup_pending')
    if doc.docstatus == 1 and missing and not legacy_incomplete:
        frappe.throw(_('Para confirmar una CxC indique Responsable, Fecha compromiso de gestión y Vencimiento de la CxC. El compromiso de gestión no determina la antigüedad.'))
