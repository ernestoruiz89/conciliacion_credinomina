"""Daily receipts followed by accounting imports; isolated site, rollback only."""
import csv
import io
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation.accounting_deposits import create_deposits, plan_deposits
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override, file_sha256
from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import import_source_file
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute as control


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'DAILY-' + frappe.generate_hash(length=8)
    tables = ['CN Remittance Allocation', 'CN Deposit Accounting Source', 'CN Accounting Import', 'File']
    before = {table: frappe.db.count(table) for table in tables}
    try:
        company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
        number = str(900000000000 + int(frappe.generate_hash(length=10), 16))
        bank = frappe.get_doc(dict(doctype='CN Bank Account', account_name=marker, bank_name='BAC',
                                  account_number=number, currency='USD', active=1)).insert()
        cut_date = next(f'{year}-01-15' for year in range(2090, 2200)
                        if not frappe.db.exists('CN Credit Portfolio Snapshot', {'cut_month': f'{year}-01', 'disabled': 0}))
        portfolio = frappe.get_doc(dict(doctype='CN Credit Portfolio Snapshot', report_date=cut_date,
            source_file=f'/private/files/{marker}-portfolio.csv',
            status='Importado', rows=[dict(credit_number=marker + '-1', employer=company.name,
                                         client_name=marker, client_number_core=marker)])).insert()

        def receipt(reference, *, confirmed=True, **changes):
            document = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                bank_account=bank.name, deposit_date='2025-05-10', deposit_reference=reference,
                deposit_currency='USD', deposit_amount=100, **changes)).insert()
            if confirmed:
                document.submit()
            return document

        def ledger(reference, credits=(100,), *, bank_number=None, company_doc=None, bank_amount=100,
                   symbol='US$', currency='USD', rate=0):
            owner = company_doc or company
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(['Fecha Aplica', 'Cuenta Contable', 'Descripción', 'TMov', 'TDoc',
                'No. Cmpte', 'No. Ref', 'Débito del Mes', 'Crédito del Mes', 'Empresa'])
            for credit in credits:
                writer.writerow(['2025-05-15', '1602', f'{symbol}{bank_amount:.2f} DEPOSITO EN LA CUENTA BAC {bank_number or number} {symbol} EL DIA 10/05/2025',
                    '02', '12', marker + '-ASIENTO', reference, 0, credit, owner.name])
            content = stream.getvalue().encode()
            file = save_file(marker + '-' + frappe.generate_hash(length=6) + '.csv', content, owner.doctype, owner.name, is_private=1)
            rows = apply_accounting_currency_override(parse_accounting_movements(file.file_name, content), currency, rate)
            identify_lines(rows, file_sha256(content))
            return file, plan_deposits(rows, [owner.as_dict()]), file_sha256(content)

        def snapshot(document):
            return {field: document.get(field) for field in ('docstatus', 'deposit_amount', 'amount_usd',
                'deposit_date', 'deposit_reference', 'deposit_voucher', 'fx_rate', 'allocated_usd',
                'unallocated_usd', 'justified_surplus_usd', 'unclassified_usd', 'allocation_detail',
                'result', 'financial_status', 'detail_hash', 'detail_file')}

        # Confirmed manual receipt, blank accounting voucher, later posting date,
        # two physical lines for the same explicit bank event.
        original = receipt(marker + '-CONFIRMED')
        original.reload()
        saved = snapshot(original)
        file, rows, digest = ledger(original.deposit_reference, (90, 10))
        assert create_deposits(rows, file.file_url, digest) == []
        original.reload()
        assert snapshot(original) == saved
        assert len(original.accounting_sources) == 2
        assert {row['remittance_allocation'] for row in rows} == {original.name}
        assert not create_deposits(rows, file.file_url, digest)
        original.reload()
        assert len(original.accounting_sources) == 2
        from credinomina_reconciliation.deposit_accounting_sources import source_parent
        assert source_parent(rows[1]['accounting_source_key']) == original.name
        frappe.db.savepoint('daily_conflicting_retry')
        try:
            create_deposits([{**rows[1], 'source_credit': 11}], file.file_url, digest)
        except frappe.ValidationError:
            frappe.db.rollback(save_point='daily_conflicting_retry')
        else:
            raise AssertionError('Un reintento aceptó evidencia contradictoria')
        assert frappe.db.count('File', {'file_url': file.file_url, 'attached_to_doctype': original.doctype,
                                      'attached_to_name': original.name}) == 1
        report = control(dict(month='2025-05-01', employer=company.name))[1]
        report = [row for row in report if row.get('remittance_allocation') == original.name]
        assert len(report) == 2 and sum(row['credit_usd'] for row in report) == 100
        # The source evidence stays immutable through the normal document API.
        original.accounting_sources[0].source_credit = 999
        frappe.db.savepoint('daily_tamper')
        try:
            original.save()
        except frappe.ValidationError:
            frappe.db.rollback(save_point='daily_tamper')
        else:
            raise AssertionError('La evidencia contable pudo modificarse')
        original.reload()
        assert original.accounting_sources[0].source_credit == 90

        # Bulk import reuses a daily draft, and a retry returns the same receipt.
        draft = receipt(marker + '-DRAFT', confirmed=False)
        file, _, _ = ledger(draft.deposit_reference)
        options = {'source_file': file.file_url, 'currency': 'USD', 'portfolio_snapshot': portfolio.name}
        result = bulk._create_imports(bulk._plan(options), options)
        assert [row['name'] for row in result] == [draft.name], result
        draft.reload()
        assert draft.docstatus == 0 and len(draft.accounting_sources) == 1
        assert [row['name'] for row in bulk._create_imports(bulk._plan(options), options)] == [draft.name]

        # The individual import's mirror also points at the daily receipt.
        single = receipt(marker + '-SINGLE')
        file, _, _ = ledger(single.deposit_reference)
        imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
            source_file=file.file_url, currency='USD', portfolio_snapshot=portfolio.name)).insert()
        from credinomina_reconciliation.file_references import attach_existing_file
        attach_existing_file(file, imported.doctype, imported.name, 'source_file')
        response = import_source_file(imported.name)
        imported = frappe.get_doc(imported.doctype, response['import_name'])
        assert len(imported.rows) == 1 and imported.rows[0].remittance_allocation == single.name
        assert not imported.rows[0].effective
        import_source_file(imported.name)
        single.reload()
        assert len(single.accounting_sources) == 1

        nio_number = number + '7'
        nio_bank = frappe.get_doc(dict(doctype='CN Bank Account', account_name=marker + '-NIO', bank_name='BAC',
                                      account_number=nio_number, currency='NIO', active=1)).insert()
        nio = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
            bank_account=nio_bank.name, deposit_date='2025-05-10', deposit_reference=marker + '-NIO',
            deposit_currency='NIO', deposit_amount=3650, fx_rate=36.5)).insert().submit()
        file, rows, digest = ledger(nio.deposit_reference, (3650,), bank_number=nio_number,
                                    bank_amount=3650, symbol='C$', currency='NIO', rate=36.6)
        assert not create_deposits(rows, file.file_url, digest)
        nio.reload()
        assert nio.fx_rate == 36.5 and nio.deposit_amount == 3650 and nio.amount_usd == 100
        assert nio.accounting_sources[0].source_currency == 'NIO' and nio.accounting_sources[0].source_fx_rate == 36.6

        # Further identical cash descriptions with excess turnover stay separate
        # for review; they cannot invalidate the confirmed daily distribution.
        excess = receipt(marker + '-EXCESS')
        file, rows, digest = ledger(excess.deposit_reference, (100, 100))
        created = create_deposits(rows, file.file_url, digest)
        assert len(created) == 1 and created[0].cash_identity_status == 'Revisar identidad'
        excess.reload()
        assert len(excess.accounting_sources) == 1 and excess.docstatus == 1
        assert excess.cash_identity_status != 'Revisar identidad'
        # A changed file is a new ledger origin, not proof of new cash.
        file, rows, digest = ledger(single.deposit_reference, (99,))
        created = create_deposits(rows, file.file_url, digest)
        assert len(created) == 1 and created[0].cash_identity_status == 'Revisar identidad'
        single.reload()
        assert len(single.accounting_sources) == 1

        # An unrelated company with no daily receipts still gets a new draft.
        newcomer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + '-NEW', employer_code=marker + '-NEW')).insert()
        file, rows, digest = ledger(marker + '-NEW', company_doc=newcomer)
        created = create_deposits(rows, file.file_url, digest)
        assert len(created) == 1 and created[0].docstatus == 0 and not created[0].cash_identity_status

        # Ambiguous/missing/conflicting evidence is an explicit review draft.
        for case in ('VOUCHER', 'BANK', 'MULTIPLE', 'REFERENCE'):
            reference = marker + '-' + case
            candidate = receipt(reference, confirmed=case != 'MULTIPLE',
                                **({'deposit_voucher': 'OTRO'} if case == 'VOUCHER' else {}))
            if case == 'MULTIPLE':
                receipt(reference, confirmed=False)
            file, rows, digest = ledger(reference + '-TYPO' if case == 'REFERENCE' else reference,
                                        bank_number='123456789012345678' if case == 'BANK' else None)
            created = create_deposits(rows, file.file_url, digest)
            assert len(created) == 1 and created[0].cash_identity_status == 'Revisar identidad', case
            candidate.reload()
            assert not candidate.accounting_sources, case
            frappe.db.savepoint('daily_review')
            try:
                created[0].submit()
            except frappe.ValidationError:
                frappe.db.rollback(save_point='daily_review')
            else:
                raise AssertionError('Se confirmó un posible duplicado: ' + case)
            assert not create_deposits(rows, file.file_url, digest)

        # Actual reconciled targets and a closed period survive evidence attachment.
        from credinomina_reconciliation.testing.smoke_receivable_recovery import fixture
        def bank_receipt(employer, reference, amount):
            return frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer,
                bank_account=bank.name, deposit_reference=reference, deposit_date='2025-05-10',
                deposit_currency='USD', deposit_amount=amount)).insert().submit()
        with patch('credinomina_reconciliation.testing.smoke_receivable_recovery.new_deposit', bank_receipt):
            owner, period, _, _, reconciled = fixture(marker + '-CLOSED')
        # fixture is US$90; use a matching explicit bank amount below.
        frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado')
        reconciled.reload()
        saved = snapshot(reconciled)
        targets = [row.as_dict() for row in reconciled.targets]
        file, rows, digest = ledger(reconciled.deposit_reference, (90,), company_doc=owner, bank_amount=90)
        assert not create_deposits(rows, file.file_url, digest)
        reconciled.reload()
        assert snapshot(reconciled) == saved
        assert [row.as_dict() for row in reconciled.targets] == targets
        assert frappe.db.get_value(period.doctype, period.name, 'status') == 'Cerrado'
        return {'manual_confirmed_and_draft': True, 'bulk_and_individual': True,
                'retry_without_duplicates': True, 'multiple_ledger_lines_one_receipt': True,
                'ambiguous_cases_blocked': True, 'ledger_report_once': True,
                'closed_distribution_preserved': True, 'immutable_evidence': True,
                'nio_receipt_keeps_original_fx': True, 'excess_and_changed_files_reviewed': True, 'rolled_back': True}
    finally:
        frappe.db.rollback()
        assert before == {table: frappe.db.count(table) for table in tables}, 'El ensayo dejó registros'
