"""Compare batched candidate balances with live individual reads; rollback only."""
import json
from time import perf_counter
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_review import application_candidates
from credinomina_reconciliation.application_adjustments import cash_coverage
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reconciliation_scope import document_state


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CANDIDATE-CASH-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            other = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + '-OTHER', employer_code=marker + '-OTHER')).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-07-01', reconciliation_mode='Historica')).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD',
                historical_backfill=1, historical_period=period.name, rows=[dict(
                    source_key=marker + str(i), event_type='Aplicacion', effective=1, currency='USD',
                    amount=100, amount_usd=100, event_date='2025-07-15', historical_period=period.name)
                    for i in range(25)])).insert()
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
                    category='Por clasificar', posting_date='2025-07-15', currency='USD', amount=10,
                    accounting_source_key=marker, source_credit=10, source_debit=0,
                    description='Candidate preview fixture')).insert()
            deposits = []
            for label, owner, status, parts, targets in [
                ('CONFIRMED', other.name, 1, [(0, 40), (1, 100)], [(0, 50)]),
                ('DRAFT', company.name, 0, [], [(2, 25)])]:
                deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=owner,
                    deposit_date='2025-07-15', deposit_reference=marker + label,
                    deposit_currency='USD', deposit_amount=200)).insert()
                # Synthetic persisted evidence, never reconciled or committed.
                for index, amount in targets:
                    target = deposit.append('targets', dict(historical_application=imported.rows[index].name,
                        amount_usd=amount, docstatus=status))
                    target.db_insert()
                ledger = [dict(tipo='Aplicacion historica', aplicacion_id=imported.rows[index].name,
                    periodo=period.name, importe_usd=amount) for index, amount in parts]
                ledger += [dict(tipo='Aplicacion historica', aplicacion_id=imported.rows[3].name + '-OTHER', importe_usd=99),
                    dict(tipo='Partida complementaria', aplicacion_id=imported.rows[3].name, importe_usd=99)]
                frappe.db.set_value(deposit.doctype, deposit.name, dict(docstatus=status,
                    allocation_detail=json.dumps(ledger)), update_modified=False)
                deposits.append(deposit.reload())
            documents = [imported.reload(), item.reload(), period.reload(), *deposits]
            before = {doc.name: document_state(doc) for doc in documents}
            expected = {row.name: cash_coverage(row)['adjustable_usd'] for row in imported.rows}
            sql, queries = frappe.db.sql, 0

            def query(*args, **kwargs):
                nonlocal queries
                queries += 1
                return sql(*args, **kwargs)

            started = perf_counter()
            with patch.object(frappe.db, 'sql', new=query):
                result = application_candidates(item.name, imported.name)
            elapsed = round(perf_counter() - started, 3)
            actual = {row['name']: row['adjustable_usd'] for row in result}
            assert actual == {name: amount for name, amount in expected.items() if amount > 0}
            assert expected[imported.rows[0].name] == 50
            assert expected[imported.rows[1].name] == 0
            assert expected[imported.rows[2].name] == 75
            assert expected[imported.rows[3].name] == 100
            assert queries < 40, queries
            for doc in documents:
                assert document_state(doc.reload()) == before[doc.name], doc.name
            return dict(applications=25, candidates=len(result), seconds=elapsed, queries=queries,
                individual_balances_identical=True, confirmed_and_draft_reservations_preserved=True,
                cross_company_cash_preserved=True, prefix_and_wrong_kind_ignored=True,
                documents_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
