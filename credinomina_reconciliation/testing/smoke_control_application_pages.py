"""Real SQL parity, routing, cents and permission-scoped child pagination."""
import frappe

from credinomina_reconciliation.control_application_pages import historical_page
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        marker = 'CONTROL-PAGE-' + frappe.generate_hash(length=8)
        company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        source = marker + '-IMPORT'
        frappe.get_doc(dict(doctype='CN Accounting Import', name=source, employer=company.name,
            status='Importado', currency='USD', historical_backfill=1)).db_insert()
        fields = ['name', 'parent', 'parenttype', 'parentfield', 'idx', 'event_type',
                  'event_date', 'currency', 'amount', 'effective', 'processing_route',
                  'application_adjustment_usd', 'historical_period', 'collection_period']
        values = [[marker + f'-R{i:03}', source, 'CN Accounting Import', 'rows', i + 1,
            'Aplicacion', '2025-04-30', 'USD', 10, 1, 'Historica', 0, '', ''] for i in range(205)]
        variations = [
            ('future-historical', '2026-09-30', 'Historica', 10, 0, 1, '', ''),
            ('future-backfill', '2026-09-30', '', 10, 0, 1, '', ''),
            ('past-operative', '2025-04-30', 'Operativa', 10, 0, 1, '', ''),
            ('future-operative', '2026-09-30', 'Operativa', 10, 0, 1, '', ''),
            ('zero-net', '2025-04-30', '', 10, 10, 1, '', ''),
            ('sub-cent-zero', '2025-04-30', '', 0.004, 0, 1, '', ''),
            ('half-cent-positive', '2025-04-30', '', 0.005, 0, 1, '', ''),
            ('already-historic', '2025-04-30', '', 10, 0, 1, 'P', ''),
            ('already-operative', '2025-04-30', '', 10, 0, 1, '', 'P'),
            ('not-effective', '2025-04-30', '', 10, 0, 0, '', ''),
        ]
        values.extend([marker + '-' + label, source, 'CN Accounting Import', 'rows', i + 206,
            'Aplicacion', when, 'USD', amount, effective, route, adjustment, historical, collection]
            for i, (label, when, route, amount, adjustment, effective, historical, collection) in enumerate(variations))
        frappe.db.bulk_insert('CN Source Row', fields=fields, values=values)
        full = control._build_control_data('Todos', company.name, summary_only=True,
            detail_section='unassigned_historical_applications')['unassigned_historical_applications']
        assert len(full) == 209, len(full)
        pages = [historical_page(None, company.name, start) for start in (0, 100, 200, 300)]
        assert [len(page['rows']) for page in pages] == [100, 100, 9, 0]
        assert all(page['count'] == len(full) for page in pages)
        rows = [row for page in pages for row in page['rows']]
        assert len({row.name for row in rows}) == len(full)
        assert {row.name for row in rows} == {row.name for row in full}
        assert historical_page(2025, company.name)['count'] == 207
        # A parent status change must hide both counts and display rows, without
        # deleting or mutating the child evidence in this read-only operation.
        frappe.db.set_value('CN Accounting Import', source, 'status', 'Borrador')
        assert historical_page(None, company.name) == {'rows': [], 'count': 0}
        assert frappe.db.count('CN Source Row', {'parent': source}) == len(values)
        return dict(rows=209, page_sizes=[100, 100, 9, 0], routing_and_cents_equal=True,
            no_duplicates_or_truncation=True, inactive_parent_excluded=True, rolled_back=True)
    finally:
        frappe.db.rollback()
