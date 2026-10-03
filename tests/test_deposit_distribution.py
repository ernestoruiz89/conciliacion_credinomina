import json
import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation import deposit_distribution as view


def deposit(**kwargs):
    return dict(name="DEP-1", docstatus=1, amount_usd=130, allocated_usd=100,
                justified_surplus_usd=30, result="Conciliado con saldo a favor del cliente",
                allocation_detail=[
                    {"tipo": "Aplicacion historica", "periodo": "P1", "aplicacion_id": "A1", "importe_usd": 40},
                    {"tipo": "Aplicacion historica", "periodo": "P1", "aplicacion_id": "A2", "importe_usd": 50},
                    {"tipo": "Partida complementaria", "partida": "X1", "importe_usd": 10},
                ], targets=[{"amount_usd": 9999, "complementary_item": "NOT-APPLIED"}]) | kwargs


def balance(name, category, amount, **kwargs):
    return dict(name=name, category=category, amount_usd=amount, registered_deposit="DEP-1",
                docstatus=1, result="Saldo a favor documentado", employer="E") | kwargs


class DepositDistributionTests(unittest.TestCase):
    def test_complete_distribution_without_using_planned_targets_or_double_counting(self):
        credits = [balance("CLIENT", view.CLIENT_CREDIT, 20, client_name="Ana", client_number="7",
                           loan_number="123-1", credit_pending_usd=6, credit_management_status="Parcialmente resuelto"),
                   balance("COMPANY", view.COMPANY_CREDIT, 10)]
        data = view.build_distribution(deposit(),
            items={"X1": dict(name="X1", category="Cobranza administrativa", employer="E", description="Gestión de cobro")},
            balances=[*credits, credits[0]], periods={"P1": dict(name="P1", employer="E")},
            people={("H", "P1", name): dict(client_name="Ana", client_number="7", loan_number="123-1") for name in ("A1", "A2")})
        self.assertEqual([r["category"] for r in data["rows"]],
                         ["Pago a crédito", "Cobranza administrativa", view.CLIENT_CREDIT, view.COMPANY_CREDIT])
        self.assertEqual([r["amount_usd"] for r in data["rows"]], [90, 10, 20, 10])
        self.assertEqual((data["total_usd"], data["distributed_usd"], data["detailed_usd"], data["pending_usd"]), (130, 130, 130, 0))
        client = data["rows"][2]
        self.assertEqual((client["state"], client["management_status"], client["management_pending_usd"]), ("Documentado", "Parcialmente resuelto", 6))
        self.assertEqual((client["record_doctype"], client["record_name"]), ("CN Complementary Item", "CLIENT"))
        self.assertTrue(data["consistent"])
        self.assertNotIn("NOT-APPLIED", str(data))

    def test_negative_complementary_and_zero_cash_tolerance_keep_their_cash_amount(self):
        data = view.build_distribution(deposit(amount_usd=90, allocated_usd=90, justified_surplus_usd=0,
            allocation_detail=[{"tipo": "Cobranza", "periodo": "P", "fila_id": "C", "importe_usd": 100},
                {"tipo": "Partida complementaria", "partida": "X", "importe_usd": -10},
                {"tipo": "Movimiento de conciliación", "movimiento": "T", "importe_usd": 0, "diferencia_usd": -0.01}]),
            items={name: dict(name=name, category="Ajuste de conciliación") for name in ("X", "T")})
        self.assertEqual([r["amount_usd"] for r in data["rows"]], [100, -10, 0])
        self.assertEqual(data["rows"][2]["difference_usd"], -0.01)
        self.assertEqual(data["detailed_usd"], 90)
        self.assertTrue(data["consistent"])

    def test_inaccessible_documents_do_not_leak_ids_names_or_customer_metadata(self):
        raw = deposit(allocation_detail=[{"tipo": "Aplicacion historica", "periodo": "SECRET-P", "aplicacion_id": "SECRET-A", "importe_usd": 90},
            {"tipo": "Partida complementaria", "partida": "SECRET-X", "empresa": "SECRET-E", "cliente": "SECRET-PERSON",
             "nro_cliente": "SECRET-N", "credito": "SECRET-LOAN", "fila_detalle": "ROW", "importe_usd": 10}])
        data = view.build_distribution(raw)
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertEqual(sum(r["amount_usd"] for r in data["rows"]), 130)
        self.assertIn("Saldo a favor (detalle no disponible)", [r["category"] for r in data["rows"]])
        self.assertTrue(data["consistent"])

    def test_confirmed_unallocated_and_drafts_canceled_are_not_distributions(self):
        for status in (0, 2):
            data = view.build_distribution(deposit(docstatus=status))
            self.assertEqual(data["rows"], [])
            self.assertEqual((data["distributed_usd"], data["pending_usd"]), (0, 130))
        data = view.build_distribution(deposit(amount_usd=130, allocated_usd=0, justified_surplus_usd=0, allocation_detail="[]"))
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["pending_usd"], 130)

    def test_invalid_or_inconsistent_evidence_is_explicit_not_silently_conciliated(self):
        data = view.build_distribution(deposit(allocation_detail="broken"))
        self.assertFalse(data["consistent"])
        self.assertEqual(data["rows"][0]["category"], "Distribución sin detalle disponible")
        credit = balance("X", view.CLIENT_CREDIT, 40)
        data = view.build_distribution(deposit(), balances=[credit])
        self.assertFalse(data["consistent"])
        self.assertEqual((data["distributed_usd"], data["detailed_usd"]), (130, 140))

    def test_canceled_undocumented_or_other_deposit_balances_do_not_participate(self):
        credits = [balance("X", view.CLIENT_CREDIT, 20, docstatus=2),
                   balance("Y", view.COMPANY_CREDIT, 10, registered_deposit="OTHER"),
                   balance("Z", view.CLIENT_CREDIT, 10, result="Revisar")]
        data = view.build_distribution(deposit(), balances=credits)
        self.assertEqual(data["rows"][-1]["category"], "Saldo a favor (detalle no disponible)")
        self.assertEqual(data["rows"][-1]["amount_usd"], 30)

    def test_api_checks_parent_permission_before_related_queries(self):
        doc = Mock()
        doc.check_permission.side_effect = PermissionError("Denied")
        with patch.object(view.frappe, "get_doc", return_value=doc), patch.object(view.frappe, "get_list") as query:
            with self.assertRaises(PermissionError):
                view.get_distribution("DEP-1")
        doc.check_permission.assert_called_once_with("read")
        query.assert_not_called()

    def test_api_queries_only_this_deposit_and_authorized_related_documents(self):
        doc = Mock(name="document", docstatus=1)
        doc.name = "DEP-1"
        doc.as_dict.return_value = deposit()
        calls = []
        def query(doctype, **kwargs):
            calls.append((doctype, kwargs))
            if doctype == "CN Reconciliation Period":
                return [dict(name="P1", employer="E")]
            if "registered_deposit" in kwargs["filters"]:
                return [balance("S1", view.CLIENT_CREDIT, 30, client_name="Ana")]
            return [dict(name="X1", category="Otros ingresos")]
        with patch.object(view.frappe, "get_doc", return_value=doc), \
                patch.object(view.frappe, "has_permission", return_value=True), \
                patch.object(view.frappe, "get_list", side_effect=query), \
                patch.object(view, "_load_credit_people", return_value={}) as people:
            data = view.get_distribution("DEP-1")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1]["filters"], {"name": ["in", ["X1"]], "docstatus": 1})
        self.assertEqual(calls[1][1]["filters"]["registered_deposit"], "DEP-1")
        self.assertEqual(calls[1][1]["filters"]["category"], ["in", [view.CLIENT_CREDIT, view.COMPANY_CREDIT]])
        self.assertEqual(data["detailed_usd"], 130)
        doc.save.assert_not_called()
        doc.db_set.assert_not_called()
        people.assert_called_once()

    def test_api_draft_or_related_permission_denied_does_not_query_private_records(self):
        doc = Mock(docstatus=0)
        doc.name = "DEP-1"
        doc.as_dict.return_value = deposit(docstatus=0)
        with patch.object(view.frappe, "get_doc", return_value=doc), patch.object(view.frappe, "get_list") as query:
            self.assertEqual(view.get_distribution("DEP-1")["rows"], [])
            query.assert_not_called()
        doc.docstatus = 1
        doc.as_dict.return_value = deposit()
        with patch.object(view.frappe, "get_doc", return_value=doc), \
                patch.object(view.frappe, "has_permission", return_value=False), \
                patch.object(view.frappe, "get_list") as query, \
                patch.object(view, "_load_credit_people", return_value={}):
            result = view.get_distribution("DEP-1")
            query.assert_not_called()
            self.assertNotIn("X1", str(result))
