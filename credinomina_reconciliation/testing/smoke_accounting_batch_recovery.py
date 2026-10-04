"""Kill only a synthetic helper process mid-block, then resume the real importer.

Restricted to the named isolated test site. Commits are required to exercise
independent connections. Cleanup selects only this run's source hash and names.
"""
import csv
import io
import os
from pathlib import Path
import subprocess
import sys
from datetime import date, timedelta
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file
from credinomina_reconciliation import accounting_batch_store as store
from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.parsers import file_sha256, parse_accounting_movements


def _crash_worker(site, sites_path, token, attempt):
    if site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.init(site=site, sites_path=sites_path)
    frappe.connect()
    frappe.set_user('Administrator')
    state = bulk._state(token)
    assert state['completed_blocks'] == 1 and state['total_blocks'] == 3
    original = bulk._create_imports

    def create_then_exit(*args, **kwargs):
        created = original(*args, **kwargs)
        assert len(created) == 25
        # Simulate worker death after writing rows/files but before committing
        # their checkpoint. MariaDB must roll back and release its row lock.
        os._exit(137)

    with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'), \
            patch.object(bulk, '_create_imports', side_effect=create_then_exit):
        bulk.run_bulk_job(token, 'Administrator', attempt=attempt)
    raise AssertionError('The synthetic crash point was not reached')


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'RECOVERY-' + frappe.generate_hash(length=10)
    source = company = token = digest = None
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert().name
            stream = io.StringIO(newline='')
            writer = csv.writer(stream)
            writer.writerow(['CUENTA_CONTABLE', 'FECHA_APLICA', 'NO_CMPTE', 'NO_REF', 'DESCRIPCION',
                             'DEBITO_DEL_MES', 'CREDITO_DEL_MES', 'EMPRESA', 'TMOV', 'TDOC'])
            choices = {}
            for index in range(56):
                writer.writerow(['123', str(date(2025, 1, 1) + timedelta(days=index)),
                                 marker + str(index), str(index), 'PAGO APLICADO SINTETICO',
                                 10, 0, 'Empresa por seleccionar', '12', '05'])
                choices[str(index + 2)] = company
            content = stream.getvalue().encode()
            source = save_file(marker + '.csv', content, None, None, is_private=1)
            digest = file_sha256(content)
            token = bulk.preview_bulk_import(source.file_url, 'USD', employer_assignments=choices,
                                            assignments_file_hash=digest)['token']
            frappe.db.commit()
            bulk.run_bulk_job(token, 'Administrator')
            assert bulk._state(token)['summary']['rows'] == 56
            bulk.confirm_bulk_import(token)
            frappe.db.commit()
            bulk.run_bulk_job(token, 'Administrator')  # Persist all three blocks.
            bulk.run_bulk_job(token, 'Administrator')  # Commit the first 25 documents.
            before = bulk._state(token)
            assert before['completed_blocks'] == 1 and before['created_count'] == 25
            frappe.db.commit()

            # Inherit the exact imported framework/app, not another bench's
            # editable package installation from the Python environment.
            app_root = str(Path(__file__).resolve().parents[2])
            framework_root = str(Path(frappe.__file__).resolve().parents[1])
            environment = dict(os.environ, PYTHONPATH=os.pathsep.join((framework_root, app_root)))
            helper = ('from credinomina_reconciliation.testing.smoke_accounting_batch_recovery '
                      'import _crash_worker; import sys; _crash_worker(*sys.argv[1:])')
            worker = subprocess.run([sys.executable, '-c', helper, frappe.local.site,
                                     str(Path(frappe.local.sites_path).resolve()), token, before['job_id']],
                                    env=environment, capture_output=True, text=True, timeout=60)
            assert worker.returncode == 137, worker.stderr
            frappe.db.rollback()  # Obtain a fresh committed snapshot.
            after = bulk._state(token)
            assert after['completed_blocks'] == 1 and after['created_count'] == 25
            assert after['options']['employer_assignments'] == choices
            assert frappe.db.count(bulk.DOCTYPE, {'bulk_source_hash': digest}) == 25
            assert bulk.get_bulk_import_status(token)['resumable']
            bulk.resume_bulk_import(token)
            frappe.db.commit()
            # A late delivery of the crashed attempt cannot consume the next block.
            bulk.run_bulk_job(token, 'Administrator', attempt=before['job_id'])
            assert bulk._state(token)['completed_blocks'] == 1
            frappe.db.rollback()
            for _ in range(2):
                bulk.run_bulk_job(token, 'Administrator')
                assert bulk._state(token)['status'] != 'Error', bulk._state(token).get('error')
            final = bulk._state(token)
            assert final['status'] == 'Completado' and final['created_count'] == 56
            parents = frappe.get_all(bulk.DOCTYPE, filters={'bulk_source_hash': digest}, pluck='name')
            assert len(parents) == 56
            rows = frappe.get_all('CN Source Row', filters={'parenttype': bulk.DOCTYPE, 'parent': ['in', parents]},
                                  fields=['source_row', 'amount_usd'], limit_page_length=0)
            assert sorted(row.source_row for row in rows) == list(range(2, 58))
            assert sum(row.amount_usd for row in rows) == 560
            for parent in parents:
                doc = frappe.get_doc(bulk.DOCTYPE, parent)
                file = frappe.get_doc('File', {'file_url': doc.source_file})
                raw = file.get_content()
                if isinstance(raw, str): raw = raw.encode()
                parsed = parse_accounting_movements(file.file_name, raw)
                assert len(parsed) == 1
                assert parsed[0]['_csv_employer_assignment'] == company
                assert parsed[0]['employer_text'] == 'Empresa por seleccionar'
            return dict(killed_helper_exit=137, committed_before_crash=25, resumed_documents=56,
                        original_rows_exact=True, applied_usd=560, employer_choices_retained=True,
                        individual_csv_verified=True, stale_delivery_ignored=True, fixtures_removed=True)
    finally:
        frappe.db.rollback()
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            if digest:
                for name in frappe.get_all(bulk.DOCTYPE, filters={'bulk_source_hash': digest}, pluck='name'):
                    frappe.delete_doc(bulk.DOCTYPE, name, ignore_permissions=True, force=True)
            if token:
                frappe.db.delete(store.BLOCK, {'batch': token})
                frappe.db.delete(store.BATCH, {'name': token})
                frappe.cache.delete_value(bulk._key(token))
            if source and frappe.db.exists('File', source.name):
                frappe.delete_doc('File', source.name, ignore_permissions=True)
            if company and frappe.db.exists('CN Employer', company):
                frappe.delete_doc('CN Employer', company, ignore_permissions=True)
            frappe.db.commit()
