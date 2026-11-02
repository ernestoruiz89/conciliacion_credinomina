"""A stale client Link cannot hide contradictory original credit beneficiaries."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import credit_application
from credinomina_reconciliation.testing.smoke_period_cash_breakdown import _credit_fixture


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            fixture, origin = _credit_fixture('CREDIT-IDENTITY-' + frappe.generate_hash(length=8))
            employer, periods, imported, credit, deposit = fixture
            original_client_number = imported.rows[0].client_number
            application_id = imported.rows[0].name
            operation = next(entry['operation_id'] for entry in json.loads(credit.credit_history)
                             if entry.get('application_id') and not entry.get('reverses'))
            credit_application.reverse(credit.name, str(credit.modified), operation,
                                       '2025-06-02', 'Ensayo de identidad')
            credit.reload(); imported.reload(); deposit.reload(); origin.reload()
            assert credit.credit_pending_usd == 10
            assert imported.rows[0].historical_remitted_usd == 90
            credit_state = credit.as_dict()
            cash_states = [doc.as_dict() for doc in (deposit, origin)]
            # Reproduce inconsistent legacy metadata, retaining the valid Link
            # while changing the original source identifier to another client.
            frappe.db.set_value('CN Source Row', application_id, 'client_number',
                                'OTHER-' + original_client_number, update_modified=False)
            assert credit_application.candidates(credit.name)['rows'] == []
            frappe.db.savepoint('reject_credit_identity')
            try:
                credit_application.apply(credit.name, str(credit.modified), application_id, 10, '2025-06-02')
            except frappe.ValidationError:
                frappe.db.rollback(save_point='reject_credit_identity')
            else:
                raise AssertionError('Una aplicación de otro número de cliente consumió el saldo')
            assert credit.reload().as_dict() == credit_state
            assert [doc.reload().as_dict() for doc in (deposit, origin)] == cash_states
            assert frappe.db.get_value('CN Source Row', application_id, 'historical_remitted_usd') == 90
            frappe.db.set_value('CN Source Row', application_id, 'client_number',
                                original_client_number, update_modified=False)
            assert [row['name'] for row in credit_application.candidates(credit.name)['rows']] == [application_id]
            result = credit_application.apply(credit.name, str(credit.modified), application_id, 10, '2025-06-02')
            assert result['pending_usd'] == result['application_pending_usd'] == 0
            assert [doc.reload().as_dict() for doc in (deposit, origin)] == cash_states
            for period in periods:
                period.reload()
                assert period.pending_usd == 0
            return dict(conflicting_number_rejected_before_credit_use=True,
                correct_identity_can_apply=True, existing_bank_journals_preserved=True, rolled_back=True)
    finally:
        frappe.db.rollback()
