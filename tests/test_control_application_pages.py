import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import control_application_pages as pages
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


class ControlApplicationPagesTests(unittest.TestCase):
    def test_permission_failure_does_not_read_children(self):
        with patch.object(pages.frappe, 'has_permission', return_value=False), \
             patch.object(pages, 'records') as parents, patch.object(pages.frappe, 'db', Mock()) as db:
            self.assertEqual(pages.historical_page(), {'rows': [], 'count': 0})
        parents.assert_not_called()
        db.sql.assert_not_called()

    def test_no_readable_parents_means_no_child_query(self):
        with patch.object(pages.frappe, 'has_permission', return_value=True), \
             patch.object(pages, 'records', return_value=[]), patch.object(pages.frappe, 'db', Mock()) as db:
            self.assertEqual(pages.historical_page(), {'rows': [], 'count': 0})
        db.sql.assert_not_called()

    def test_page_loads_only_100_rows_but_counts_every_readable_row(self):
        rows = [frappe._dict(name='R', employer='A')]
        db = Mock(sql=Mock(side_effect=[[(26000,)], rows]))
        with patch.object(pages.frappe, 'has_permission', return_value=True), \
             patch.object(pages, 'records', return_value=[frappe._dict(name='READABLE')]) as parents, \
             patch.object(pages.frappe, 'db', db):
            self.assertEqual(pages.historical_page(2025, 'A', 25900), {'rows': rows, 'count': 26000})
        self.assertEqual(parents.call_args.kwargs['filters']['employer'], 'A')
        for query in db.sql.call_args_list:
            sql, values = query.args
            self.assertEqual(values['parents'], ('READABLE',))
            self.assertEqual(values['date_from'], '2025-01-01')
            self.assertIn("s.parentfield='rows'", sql)
            self.assertIn("s.parenttype='CN Accounting Import'", sql)
            self.assertIn("coalesce(s.collection_period, '')=''", sql)
            self.assertIn("coalesce(s.historical_period, '')=''", sql)
            self.assertIn('round(coalesce(s.amount, 0), 2)', sql)
            self.assertIn('s.event_date < %(operative_start)s', sql)
            self.assertIn("!= 'Operativa'", sql)
        self.assertIn('s.name asc limit 100 offset %(start)s', db.sql.call_args.args[0])
        self.assertEqual(db.sql.call_args.args[1]['start'], 25900)

    def test_all_years_omit_date_bounds_and_exhausted_page_skips_row_query(self):
        db = Mock(sql=Mock(return_value=[(100,)]))
        with patch.object(pages.frappe, 'has_permission', return_value=True), \
             patch.object(pages, 'records', return_value=[frappe._dict(name='P')]), \
             patch.object(pages.frappe, 'db', db):
            self.assertEqual(pages.historical_page(None, start=100), {'rows': [], 'count': 100})
        self.assertEqual(db.sql.call_count, 1)
        self.assertNotIn('date_from', db.sql.call_args.args[1])

    def test_endpoint_does_not_build_dashboard_to_page_applications(self):
        with patch.object(control.frappe, 'has_permission', return_value=True), \
             patch.object(pages, 'historical_page', return_value={'rows': [], 'count': 200}) as page, \
             patch.object(control, '_build_control_data') as build:
            self.assertEqual(control.get_control_rows('unassigned_historical_applications', 'Todos', 'A', 100),
                             {'rows': [], 'count': 200})
        page.assert_called_once_with(None, 'A', 100)
        build.assert_not_called()

    def test_endpoint_rejects_bad_year_or_unreadable_control(self):
        with patch.object(control.frappe, 'has_permission', return_value=True), \
             patch.object(control.frappe, 'throw', side_effect=ValueError), \
             patch.object(pages, 'historical_page') as page:
            with self.assertRaises(ValueError):
                control.get_control_rows('unassigned_historical_applications', '1999')
        page.assert_not_called()
        with patch.object(control.frappe, 'has_permission', return_value=False), \
             patch.object(control.frappe, 'throw', side_effect=PermissionError), \
             patch.object(pages, 'historical_page') as page:
            with self.assertRaises(PermissionError):
                control.get_control_rows('unassigned_historical_applications', 'Todos')
        page.assert_not_called()
