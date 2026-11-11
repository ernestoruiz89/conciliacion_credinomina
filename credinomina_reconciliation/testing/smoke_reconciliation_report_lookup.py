"""Rollback-only SQL parity for the summary report's batched reads."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import reconciliation_report as report
from credinomina_reconciliation.credit_lookup import get_credit_rows


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        marker = 'REPORT-LOOKUP-' + frappe.generate_hash(length=8)
        frappe.get_doc(dict(doctype='CN Credit Portfolio Snapshot', name=marker,
                            report_date='2026-05-31', status='Importado')).db_insert()
        variants = ['1', '01', '00001-1', '1-2', '0001-2', '1-01', ' 1-1 ', "'1-1",
                    '1-1\n', '0', '000', '000-1', '000-2', 'ABC.7', 'abcX7', 'a+b', 'ab',
                    'A_B', '[1]', 'K(1)', 'A\\B', 'I', 'İ', 'ı', 'ß', 'SS', 'Σ', 'ς', 'σ']
        variants += [str(i) for i in range(10000, 10502)]
        loans = ['1', '1-2', '0', 'abc.7', 'a+b', 'a_b', '[1]', 'k(1)', 'a\\b', 'i', 'ss', 'σ']
        loans += [str(i) for i in range(10000, 10502)]
        frappe.db.bulk_insert('CN Credit Portfolio Row', fields=[
            'name', 'parent', 'parenttype', 'parentfield', 'credit_number', 'credit_status', 'employer'],
            values=[[f'{marker}-{i}', marker, 'CN Credit Portfolio Snapshot', 'rows', value, 'Activo', 'TEST']
                    for i, value in enumerate(variants)])
        parents = [f'{marker}-IMPORT-{i:04}' for i in range(501)]
        frappe.db.bulk_insert('CN Accounting Import', fields=['name'], values=[[parent] for parent in parents])
        # Several children per parent must not repeat or truncate dependencies.
        frappe.db.bulk_insert('CN Source Row', fields=[
            'name', 'parent', 'parenttype', 'parentfield', 'historical_period', 'event_type', 'effective', 'match_status'],
            values=[[f'{parent}-{i}', parent, 'CN Accounting Import', 'rows', marker, 'Aplicacion', 1, 'Conciliado']
                    for parent in parents for i in range(2)])

        sql = frappe.db.sql

        def read_only(query, *args, **kwargs):
            assert not str(query).lstrip().lower().startswith(('insert ', 'update ', 'delete ', 'replace '))
            return sql(query, *args, **kwargs)

        with patch.object(frappe.db, 'sql', side_effect=read_only):
            original = get_credit_rows('CN Credit Portfolio Row', filters={
                'parent': marker, 'parenttype': 'CN Credit Portfolio Snapshot', 'parentfield': 'rows'},
                fields=['credit_number', 'credit_status', 'employer'], loan_field='credit_number', loans=loans)
            optimized = report._portfolio_rows(marker, loans)
        key = lambda row: (row['credit_number'], row['credit_status'], row['employer'])
        assert sorted(map(key, original)) == sorted(map(key, optimized))

        mode = frappe.db.sql('select @@session.sql_mode')[0][0]
        frappe.db.sql('SET SESSION sql_mode = %s', (mode + ',ONLY_FULL_GROUP_BY',))
        try:
            with patch.object(frappe.db, 'sql', side_effect=read_only):
                dependencies = list(report.records_for_dependencies({
                    'historical_period': marker, 'event_type': 'Aplicacion', 'effective': 1,
                    'match_status': 'Conciliado'}))
            assert [row.parent for row in dependencies] == parents
        finally:
            frappe.db.sql('SET SESSION sql_mode = %s', (mode,))
        return dict(credits=len(optimized), parents=len(dependencies), read_only=True)
    finally:
        frappe.db.rollback()
