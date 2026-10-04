"""Real paginated report reads; synthetic data, test site only, always rolled back."""
import frappe
from unittest.mock import patch


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'PAGING-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc({'doctype': 'CN Employer', 'employer_name': marker, 'employer_code': marker}).insert()
            period = frappe.get_doc({'doctype': 'CN Reconciliation Period', 'employer': employer.name,
                'payroll_month': '2026-09-01', 'reconciliation_mode': 'Operativa', 'collection_cycle': 'Mensual'}).insert()
            for index in range(1001):
                frappe.db.sql('''INSERT INTO `tabCN Collection Row`
                    (name, parent, parenttype, parentfield, idx, client_name, loan_number, expected_usd, deducted_usd, deduction_status)
                    VALUES (%s, %s, 'CN Reconciliation Period', 'collection_rows', %s, 'Cliente sintético', '1-1', 1, 0, 'No deducido')''',
                    (marker + str(index), period.name, index + 1))
            from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute as aging
            from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import execute as reconciliation
            result = aging({'employer': employer.name, 'balance_type': 'Cobranza no deducida (informativo)', 'as_of_date': '2026-10-03'})
            assert len(result[1]) == 1001, len(result[1])
            assert result[4][0]['value'] == 1001, result[4]
            result = reconciliation({'employer': employer.name})
            assert len(result[1]) == 1
            assert result[1][0]['employee_shortfall_usd'] == 1001
            return {'ok': True, 'rows': 1001, 'multiple_child_pages': True, 'report_totals': 1001, 'rolled_back': True}
    finally:
        frappe.db.rollback()
