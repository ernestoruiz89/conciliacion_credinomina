import json
import unittest
from datetime import date

from credinomina_reconciliation.application_aging import application_balances, deposit_due_date
from credinomina_reconciliation.rounding import sum_money


class ApplicationAgingTests(unittest.TestCase):
    def setUp(self):
        self.imports = {"I": {"name": "I", "employer": "E"}}
        self.periods = {
            "H": {"name": "H", "employer": "E", "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"},
            "P": {"name": "P", "employer": "E", "payroll_month": "2026-09-01", "reconciliation_mode": "Operativa"},
        }
        self.employers = {"E": {"grace_days": 10}}
        self.collections = {"C": {"name": "C", "parent": "P", "client_name": "Cliente de prueba"}}

    def source(self, **values):
        return dict({"name": "A", "parent": "I", "event_type": "Aplicacion", "effective": 1,
                     "currency": "USD", "amount": 100, "event_date": "2025-04-30",
                     "historical_period": "H", "match_status": "Conciliado"}, **values)

    def operative(self, **values):
        return self.source(**dict({"historical_period": "", "event_date": "2026-09-30",
                                  "collection_row_id": "C"}, **values))

    def balances(self, sources, as_of="2026-09-29"):
        return application_balances(sources, self.imports, self.periods, self.collections, self.employers, as_of)

    def test_six_unpaid_historical_applications_total_161_77(self):
        rows = self.balances([self.source(amount=amount) for amount in (16.21, 17.70, 27.14, 21.71, 20.40, 58.61)])
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum_money(row["amount_usd"] for row in rows), sum_money([161.77]))
        self.assertTrue(all(row["due_date"] == date(2025, 5, 10) for row in rows))
        self.assertTrue(all(row["days_over_90"] == row["amount_usd"] for row in rows))

    def test_direct_operative_cash_does_not_require_payroll_or_change_modality(self):
        source = self.operative(historical_period="P", processing_route="Operativa", historical_remitted_usd=60,
                                historical_detail='[{"importe_usd":60}]')
        row = self.balances([source])[0]
        self.assertEqual(row["amount_usd"], 40)
        self.assertEqual(row["reconciliation_mode"], "Operativa")
        source["historical_remitted_usd"] = 100
        self.assertEqual(self.balances([source]), [])

    def test_due_date_is_application_month_not_payroll_or_import_month(self):
        row = self.balances([self.operative(event_date="2026-10-15")])[0]
        self.assertEqual(row["due_date"], date(2026, 11, 10))
        self.assertEqual(row["not_due"], 100)
        self.assertEqual(deposit_due_date("2026-12-31", 15), date(2027, 1, 15))
        self.assertEqual(deposit_due_date("2024-01-31", 29), date(2024, 2, 29))
        self.assertIsNone(deposit_due_date(None))

    def test_receivable_is_core_less_confirmed_adjustment_and_cash_never_collection(self):
        for expected, deducted in ((0, 0), (1000, 1000), (1000, 0)):
            with self.subTest(expected=expected, deducted=deducted):
                self.collections['C'].update(expected_usd=expected, deducted_usd=deducted,
                    complementary_usd=500, deduction_status='Importes inconsistentes',
                    remittance_detail=json.dumps([{'destino': 'Cobranza', 'importe_usd': 30},
                        {'destino': 'Partida complementaria', 'importe_usd': 500}]))
                row = self.balances([self.operative(amount=100, application_adjustment_usd=20)])[0]
                self.assertEqual((row['applied_usd'], row['paid_usd'], row['amount_usd']), (80, 30, 50))
        historical = self.balances([self.source(amount=100, application_adjustment_usd=20, historical_remitted_usd=30)])[0]
        self.assertEqual(historical['amount_usd'], 50)
        # An unconfirmed adjustment has not changed the source's confirmed reduction.
        historical = self.balances([self.source(amount=100, application_adjustment_usd=0, historical_remitted_usd=30)])[0]
        self.assertEqual(historical['amount_usd'], 70)

    def test_grace_deadline_inclusive_and_each_employers_own_setting(self):
        source = self.source()
        self.assertEqual(self.balances([source], "2025-05-10")[0]["not_due"], 100)
        row = self.balances([source], "2025-05-11")[0]
        self.assertEqual(row["age_days"], 1)
        self.employers["E"]["grace_days"] = 20
        self.assertEqual(self.balances([source], "2025-05-11")[0]["not_due"], 100)

    def test_historical_partial_fully_paid_excess_and_signed_rounding(self):
        row = self.balances([self.source(historical_remitted_usd=30)])[0]
        self.assertEqual((row["applied_usd"], row["paid_usd"], row["amount_usd"]), (100, 30, 70))
        self.assertEqual(self.balances([self.source(historical_remitted_usd=100)]), [])
        self.assertEqual(self.balances([self.source(historical_remitted_usd=110)]), [])
        for applied, paid, delta in ((46.53, 46.52, -0.01), (46.52, 46.53, 0.01)):
            self.assertEqual(self.balances([self.source(
                amount=applied, historical_remitted_usd=paid,
                historical_detail=json.dumps([{"diferencia_usd": delta}]),
            )]), [])

    def test_operational_does_not_require_deduction_detail_and_counts_cash_once(self):
        self.collections["C"].update(deduction_status="Pendiente de detalle", remittance_detail=json.dumps([
            {"destino": "Cobranza", "importe_usd": 30}, {"destino": "Cobranza", "importe_usd": 10},
        ]))
        rows = self.balances([self.operative(amount=60, match_status="Enlace provisional"),
                              self.operative(amount=40, event_date="2026-09-15")])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["applied_usd"], rows[0]["paid_usd"], rows[0]["amount_usd"]), (100, 40, 60))
        self.assertEqual(rows[0]["due_date"], date(2026, 10, 10))

    def test_one_core_application_split_over_two_payrolls_not_duplicated(self):
        self.collections["C2"] = {"name": "C2", "parent": "P", "remittance_detail": json.dumps([
            {"destino": "Cobranza", "importe_usd": 20}])}
        row = self.operative(application_allocation_detail=json.dumps([
            {"collection_row_id": "C", "amount_usd": 40},
            {"collection_row_id": "C2", "amount_usd": 60},
        ]))
        result = self.balances([row])
        self.assertEqual([r["amount_usd"] for r in result], [40, 40])
        self.assertEqual(sum_money(r["applied_usd"] for r in result), 100)

    def test_complementary_cash_excluded_but_unpaid_complement_not_subtracted(self):
        self.collections["C"].update(complementary_usd=10, remittance_detail=json.dumps([
            {"destino": "Cobranza", "importe_usd": 80},
            {"destino": "Partida complementaria", "importe_usd": 5},
        ]))
        self.assertEqual(self.balances([self.operative(amount=90)])[0]["amount_usd"], 10)
        # Signed supplementary items can fund a full claim: loan 100, adjustment -10, bank 90.
        self.collections["C"]["remittance_detail"] = json.dumps([
            {"destino": "Cobranza", "importe_usd": 100},
            {"destino": "Partida complementaria", "importe_usd": -10},
        ])
        self.assertEqual(self.balances([self.operative()]), [])

    def test_fx_in_review_is_not_treated_as_payment_and_tolerance_is(self):
        self.collections["C"].update(fx_variance_usd=0.5, remittance_detail=json.dumps([
            {"destino": "Cobranza", "importe_usd": 99.5}]))
        self.assertEqual(self.balances([self.operative()])[0]["amount_usd"], 0.5)
        self.collections["C"].update(fx_variance_usd=0, rounding_adjustment_usd=-0.01,
                                    remittance_detail=json.dumps([{"destino": "Cobranza", "importe_usd": 99.99}]))
        self.assertEqual(self.balances([self.operative()]), [])

    def test_unlinked_applications_visible_duplicates_ignored_and_conversion_required(self):
        self.assertEqual(self.balances([self.source(effective=0), self.source(match_status="Ignorado")]), [])
        row = self.balances([self.operative(collection_row_id="", match_status="Sin coincidencia")])[0]
        self.assertEqual(row["amount_usd"], 100)
        self.assertIn("pendiente de vincular", row["observation"])
        row = self.balances([self.source(currency="NIO", amount=824.78, manual_fx_rate=36.6243)])[0]
        self.assertEqual(row["amount_usd"], 22.52)
        row = self.balances([self.source(currency="NIO")])[0]
        self.assertNotIn("amount_usd", row)
        self.assertIn("Falta tipo de cambio", row["observation"])

    def test_mixed_month_partial_cash_keeps_total_but_does_not_invent_aging(self):
        sources = [self.operative(amount=50), self.operative(amount=50, event_date="2026-10-15")]
        self.assertEqual(len(self.balances(sources)), 2)
        self.collections["C"]["remittance_detail"] = json.dumps([{"destino": "Cobranza", "importe_usd": 40}])
        rows = self.balances(sources)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["without_date"], 60)
        self.assertIn("falta distribuir", rows[0]["observation"])

    def test_unreadable_period_or_source_is_not_exposed_as_unlinked(self):
        self.assertEqual(self.balances([self.source(historical_period="PRIVATE")]), [])
        self.assertEqual(self.balances([self.source(parent="PRIVATE")]), [])
        self.assertEqual(self.balances([self.operative(collection_row_id="PRIVATE")]), [])


if __name__ == "__main__":
    unittest.main()
