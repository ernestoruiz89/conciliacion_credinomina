import unittest
from unittest.mock import Mock, patch
import frappe
from credinomina_reconciliation.complementary_balances import financial_balance, get_balance


class ComplementaryBalanceTests(unittest.TestCase):
    def test_form_endpoint_accepts_frappe_document_not_only_dict(self):
        values = {"name": "C", "category": "Otros ingresos", "amount_usd": 100, "docstatus": 1}
        document = Mock(get=lambda field: values.get(field))
        document.name = "C"
        with patch.object(frappe, "get_doc", return_value=document), patch.object(frappe, "get_list", return_value=[]):
            result = get_balance("C")
        document.check_permission.assert_called_once_with("read")
        self.assertEqual(result["pending_usd"], 100)

    def test_cash_balance_counts_signed_actual_allocations_only(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                result = financial_balance({"category": "Ajuste de conciliación", "docstatus": 1, "amount_usd": sign * 500,
                    "accounting_status": "Registrada"}, [{"amount_usd": sign * 350}])
                self.assertEqual((result["used_usd"], result["pending_usd"], result["financial_status"]), (350, 150, "Parcial"))
        result = financial_balance({"category": "Otros ingresos", "docstatus": 1, "amount_usd": 10}, [{"amount_usd": 11}])
        self.assertEqual(result["financial_status"], "Revisar distribución")

    def test_credit_explained_does_not_hide_refund_due(self):
        for category in ("Saldo a favor del cliente", "Saldo a favor de la empresa"):
            result = financial_balance({"category": category, "docstatus": 1, "amount_usd": 100,
                "result": "Saldo a favor documentado", "credit_management_status": "Parcialmente resuelto", "credit_pending_usd": 40})
            self.assertEqual((result["pending_usd"], result["management_pending_usd"]), (0, 40))

    def test_adjustment_residual_is_not_silently_discarded(self):
        result = financial_balance({"category": "Ajuste de aplicación", "docstatus": 1, "amount_usd": 100,
            "related_application": "R", "application_adjustment_usd": 60})
        self.assertEqual((result["pending_usd"], result["financial_status"]), (40, "Parcial"))

    def test_registered_is_not_conciliated_and_offset_not_a_deposit(self):
        result = financial_balance({"category": "Otros ingresos", "docstatus": 1, "amount_usd": 100, "accounting_status": "Registrada"})
        self.assertEqual(result["financial_status"], "Pendiente")
        result = financial_balance({"category": "Compensación entre partidas", "amount_usd": -100, "compensated_usd": 25})
        self.assertEqual((result["used_label"], result["pending_usd"]), ("Compensado", 75))
