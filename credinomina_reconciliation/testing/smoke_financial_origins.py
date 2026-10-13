"""Four review findings, real SQL and controllers, isolated site, always rollback."""
import csv
import io
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_deposits import create_deposits, plan_deposits, EVIDENCE_FIELDS
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.deposit_accounting_sources import link_duplicate_origin, verify_independent_deposit
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override, file_sha256
from credinomina_reconciliation.patches.v1_0.review_financial_origins import execute as migrate_origins
from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute as control


def rejects(action):
    frappe.db.savepoint('financial_origins_rejection')
    try:
        action()
    except frappe.ValidationError:
        frappe.db.rollback(save_point='financial_origins_rejection')
    else:
        raise AssertionError('La operación debía ser rechazada')


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    frappe.set_user('Administrator')
    doctypes = ['CN Employer', 'CN Client', 'CN Accounting Import', 'CN Source Row',
        'CN Reconciliation Period', 'CN Remittance Allocation', 'CN Deposit Accounting Source', 'CN Complementary Item']
    before = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    results = {}
    try:
        marker = 'ORIGINS-' + frappe.generate_hash(length=8)
        company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['Fecha Aplica', 'Cuenta Contable', 'Descripción', 'TMov', 'TDoc', 'No. Cmpte', 'No. Ref', 'Débito del Mes', 'Crédito del Mes', 'Empresa'])
        for amount in [90, 10]:
            writer.writerow(['2025-04-04', '160209013004', f'US$100.00 DEPOSITO POR CONVENIO {marker} EN LA CUENTA BAC 99999 U$ EL DIA 04/04/2025', '02', '12', marker, marker, 0, amount, marker])
        content = stream.getvalue().encode()
        rows = apply_accounting_currency_override(parse_accounting_movements('test.csv', content), 'USD', 0)
        identify_lines(rows, file_sha256(content))
        planned = plan_deposits(rows, [company.as_dict()])
        with patch('credinomina_reconciliation.file_references.attach_existing_file'):
            created = create_deposits(planned, '/private/files/synthetic-origins.csv', file_sha256(content))
            assert len(created) == 1
            assert not create_deposits(planned, '/private/files/synthetic-origins.csv', file_sha256(content))
        deposit = frappe.get_doc(created[0].doctype, created[0].name)
        assert len(deposit.accounting_sources) == 2 and deposit.amount_usd == 100
        assert {row['remittance_allocation'] for row in planned} == {deposit.name}
        assert summarize(build_detail([], [], [], [deposit.as_dict()]))[0]['deposit_pending_usd'] == -100
        data = control(dict(from_date='2025-04-01', to_date='2025-04-30', employer=company.name))[1]
        assert len(data) == 2 and sum(row['credit_usd'] for row in data) == 100, data
        # Even when only one accounting-import mirror exists, the second physical
        # origin must remain in the report, not disappear behind its parent's ID.
        imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name, currency='USD', status='Importado',
            source_file='/private/files/synthetic-origins.csv', rows=[dict(rows[0], effective=0, remittance_allocation=deposit.name)])).insert()
        data = control(dict(from_date='2025-04-01', to_date='2025-04-30', employer=company.name))[1]
        assert len(data) == 2 and sum(row['credit_usd'] for row in data) == 100
        deposit.submit()
        tampered = frappe.get_doc(deposit.doctype, deposit.name)
        tampered.accounting_sources[1].source_credit = 999
        rejects(tampered.save)
        assert frappe.get_doc(deposit.doctype, deposit.name).accounting_sources[1].source_credit == 10
        results['one_cash_many_origins'] = results['immutable_origins_and_idempotence'] = True

        # Simulate pre-upgrade drafts, each holding one original source line.
        old = []
        for idx, amount in enumerate([90, 10]):
            values = dict(rows[idx], source_date='2025-05-04', source_file_hash=marker + '-OLD',
                accounting_source_key=marker + '-OLD-' + str(idx))
            old.append(frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                bank_account='NO IDENTIFICADA', deposit_date='2025-05-04', deposit_reference=marker + '-OLD',
                deposit_voucher=marker + '-OLD', deposit_currency='USD', deposit_amount=100,
                **{field: values.get(field) for field in EVIDENCE_FIELDS})).insert())
        for doc in old:
            doc.reload()
        cash_before = [(doc.name, doc.amount_usd, doc.allocated_usd, doc.allocation_detail) for doc in old]
        migrate_origins()
        for doc in old:
            doc.reload()
            assert doc.cash_identity_status == 'Revisar identidad'
        assert summarize(build_detail([], [], [], [doc.as_dict() for doc in old]))[0]['balance_usd'] is None
        migrate_origins()
        assert [(doc.name, doc.amount_usd, doc.allocated_usd, doc.allocation_detail) for doc in old] == cash_before
        link_duplicate_origin(old[1].name, old[0].name, str(old[1].modified), 'Dos líneas del mismo comprobante bancario de ensayo')
        old[0].reload(); old[1].reload()
        verify_independent_deposit(old[0].name, str(old[0].modified), 'Se verificó un solo efectivo US$100 en el banco')
        old[0].reload()
        assert len(old[0].accounting_sources) == 2 and old[1].consolidated_deposit == old[0].name
        assert summarize(build_detail([], [], [], [doc.as_dict() for doc in old]))[0]['balance_usd'] == -100
        rejects(old[1].submit)
        old[0].submit()
        extra_values = dict(rows[1], source_date='2025-06-04', source_row=3, source_credit=5,
            source_file_hash=marker + '-OLD', accounting_source_key=marker + '-EXTRA')
        extra = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
            bank_account='NO IDENTIFICADA', deposit_date='2025-05-04', deposit_reference=marker + '-OLD',
            deposit_voucher=marker + '-OLD', deposit_currency='USD', deposit_amount=100,
            **{field: extra_values.get(field) for field in EVIDENCE_FIELDS})).insert()
        link_duplicate_origin(extra.name, old[0].name, str(extra.modified), 'Origen adicional del mismo efectivo, contabilizado otro mes')
        june = control(dict(from_date='2025-06-01', to_date='2025-06-30', employer=company.name))[1]
        assert len(june) == 1 and june[0]['credit_usd'] == 5
        original = frappe.get_doc(old[0].doctype, old[0].name)
        assert original.amount_usd == 100 and len(original.accounting_sources) == 3 and original.docstatus == 1
        results['legacy_review_without_financial_rewrite'] = True

        company2, period, applications, receivable, source_deposit = fixture(marker + '-CXC')
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import execute as aging
        receivable.reload()
        assert str(receivable.receivable_due_date) == '2025-05-10'
        receivable.credit_commitment_date = '2027-01-01'
        receivable.save()
        row = next(row for row in aging(dict(employer=company2.name, as_of_date='2026-10-12'))[1]
                   if row.get('complementary_item') == receivable.name)
        assert row['age_bucket'] == 'days_over_90' and str(row['due_date']) == '2025-05-10', row
        change = frappe.get_doc(receivable.doctype, receivable.name)
        change.receivable_due_date = '2027-01-01'
        rejects(change.save)
        change.reload(); change.credit_assigned_to = None
        rejects(change.save)
        results['financial_due_frozen_management_required'] = True

        # Historical membership stays on accounting evidence; the client master
        # now belongs to another employer and must never be moved back by this flow.
        current = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + '-NEW', employer_code=marker + '-NEW')).insert()
        number = str(990000 + int(frappe.generate_hash(length=5), 16) % 99999)
        client = frappe.get_doc(dict(doctype='CN Client', employer=company2.name, client_number=number, client_name='Cliente histórico de ensayo')).insert()
        loan = str(int(number) + 100000) + '-1'
        applied = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company2.name, currency='USD', status='Importado',
            source_file='/private/files/synthetic-client.csv', rows=[dict(event_type='Aplicacion', event_date='2025-06-30', currency='USD',
                amount=30, amount_usd=30, effective=1, client=client.name, client_number=number, client_name=client.client_name, loan_number=loan)])).insert()
        client.employer = current.name; client.save()
        historical_deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company2.name, deposit_date='2025-07-10',
            deposit_reference=marker + '-CLIENT', deposit_currency='USD', deposit_amount=40, detail_rows=[dict(client_name=client.client_name,
                client_number=number, loan_number=loan, deducted_usd=40)])).insert()
        historical_deposit.submit()
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import create_complementary_item
        result = create_complementary_item(historical_deposit.name, str(historical_deposit.modified), dict(category='Saldo a favor del cliente',
            posting_date='2025-07-10', currency='USD', amount=10, credit_client=client.name, client_number=number, loan_number=loan,
            credit_detail_row=historical_deposit.detail_rows[0].name, credit_treatment='Pendiente de decisión',
            credit_assigned_to='Administrator', credit_commitment_date='2026-10-31', description='Excedente histórico'))
        credit = frappe.get_doc('CN Complementary Item', result['name'])
        assert credit.employer == company2.name and frappe.db.get_value('CN Client', client.name, 'employer') == current.name
        results['historical_client_credit_without_master_mutation'] = True
        return results
    finally:
        frappe.db.rollback()
        assert before == {doctype: frappe.db.count(doctype) for doctype in doctypes}, 'El ensayo dejó registros'
