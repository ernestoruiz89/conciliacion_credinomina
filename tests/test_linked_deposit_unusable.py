from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import deposit_accounting_sources as sources
from credinomina_reconciliation import remittance_selection
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as remittance


def reject(message, *args):
    raise ValueError(message)


@pytest.mark.parametrize("link", [{"consolidated_deposit": "MAIN"}, {"cash_identity_status": "Origen vinculado"}])
@pytest.mark.parametrize("use", [{"docstatus": 1}, {"targets": [{"amount_usd": 10}]}, {"detail_file": "detail.csv"},
                                 {"detail_rows": [{"amount_usd": 10}]}, {"detail_periods": [{"period": "P"}]}])
def test_linked_origin_cannot_be_submitted_or_given_distribution_inputs(link, use):
    doc = frappe._dict({"docstatus": 0, **link, **use})
    with patch.object(frappe, "throw", side_effect=reject):
        with pytest.raises(ValueError, match="origen contable de otro depósito"):
            remittance.CNRemittanceAllocation._validate_deposit(doc)


def test_primary_receipt_remains_usable_with_many_accounting_sources():
    sources.assert_usable_cash(frappe._dict(accounting_sources=[{"accounting_source_key": str(i)} for i in range(104)]))


def test_linked_origin_cannot_create_complement_or_credit_through_server_endpoint():
    doc = frappe._dict(consolidated_deposit="MAIN", check_permission=Mock())
    with patch.object(frappe.db, "get_value", return_value=None), patch.object(frappe, "get_doc", return_value=doc), \
         patch.object(frappe, "throw", side_effect=reject), patch.object(frappe, "new_doc") as create:
        with pytest.raises(ValueError, match="depósito principal"):
            remittance.create_complementary_item("ORIGIN", "modified", {"category": "Saldo a favor de la empresa"})
        create.assert_not_called()


@pytest.mark.parametrize("endpoint", [remittance.import_remittance_detail, remittance_selection.get_pending_targets])
def test_linked_origin_cannot_load_detail_or_select_targets(endpoint):
    doc = frappe._dict(consolidated_deposit="MAIN", check_permission=Mock())
    with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "throw", side_effect=reject):
        with pytest.raises(ValueError, match="depósito principal"):
            endpoint("ORIGIN")
