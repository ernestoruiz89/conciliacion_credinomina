import unittest
from copy import deepcopy
from datetime import datetime

from credinomina_reconciliation.accounting_batch import accounting_group_csv, group_applications, movement_key
from credinomina_reconciliation.parsers import apply_accounting_currency_override, parse_accounting_movements, read_table


EMPLOYERS = [
    {"name": "A", "employer_name": "Empresa A", "employer_code": "100", "aliases": ["Planilla A"]},
    {"name": "B", "employer_name": "Empresa B", "employer_code": "200"},
]


def movement(**kwargs):
    return {"event_type": "Aplicacion", "event_date": "2025-04-15", "employer_text": "A",
            "source_row": 2, "loan_number": "13375-1", "voucher": "AS-1", "reference": "REF-1",
            "currency": "USD", "amount": 25, "amount_usd": 25, **kwargs}


class AccountingBatchTest(unittest.TestCase):
    def test_group_csv_preserves_original_money_identifiers_and_extra_columns(self):
        raw = {
            25: {"cuenta_contable": "123", "fecha_aplica": datetime(2025, 4, 15, 12),
                 "no_cmpte": "00123", "no_ref": "00044", "no_credito": "013375-1",
                 "descripcion": 'NOTA AL PRESTAMO 013375-1 PAGO APLICADO, "extra"\nDetalle',
                 "nombre_cliente": "María, Pérez", "empresa": "A", "debito_del_mes": 824.78,
                 "credito_del_mes": 0, "tmov": "12", "tdoc": "05", "columna_adicional": "=1+1"},
            26: {"cuenta_contable": "123", "fecha_aplica": "2025-05-30", "empresa": "B"},
        }
        content = accounting_group_csv(raw, [{"source_row": 25, "amount_usd": 22.52}])
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        table = read_table("individual.csv", content)
        self.assertEqual(len(table), 2)
        self.assertIn("COLUMNA_ADICIONAL", table[0])
        self.assertIn("'=1+1", table[1])
        self.assertEqual(table[1][-1], "25")
        rows = parse_accounting_movements("individual.csv", content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 824.78)  # not already-converted USD
        self.assertEqual(rows[0]["voucher"], "00123")
        self.assertEqual(rows[0]["loan_number"], "013375-1")
        self.assertEqual(rows[0]["client_name"], "María, Pérez")
        self.assertEqual(str(rows[0]["event_date"]), "2025-04-15")
        apply_accounting_currency_override(rows, "NIO", "36.6243")
        self.assertEqual(rows[0]["amount_usd"], 22.52)
        self.assertEqual(rows[0]["amount_nio"], 824.78)

    def test_groups_by_company_and_exact_day_across_months_and_years(self):
        records = [movement(event_date=day, employer_text=company)
                   for day in ("2025-04-15", "2025-04-30", "2025-05-03", "2026-04-15")
                   for company in ("A", "B")]
        records.append(movement(loan_number="13376-1"))
        plan = group_applications(records, EMPLOYERS)
        self.assertEqual(len(plan["groups"]), 8)
        first = plan["groups"][0]
        self.assertEqual((first["employer"], first["event_date"], first["count"], first["total_usd"]),
                         ("A", "2025-04-15", 2, 50))
        self.assertEqual(plan["issues"], [])

    def test_company_can_come_from_code_alias_or_portfolio(self):
        for row in (movement(employer_text="100"), movement(employer_text="Planilla A"),
                    movement(employer_text="", portfolio_employer="A")):
            self.assertEqual(group_applications([row], EMPLOYERS)["groups"][0]["employer"], "A")

    def test_missing_date_is_never_replaced_by_today(self):
        for value in (None, "", "2025-02-31"):
            result = group_applications([movement(event_date=value)], EMPLOYERS)
            self.assertEqual(len(result["issues"]), 1)
            self.assertEqual(result["groups"], [])

    def test_unknown_employers_are_grouped_without_inventing_original_text(self):
        for row in (movement(employer_text="Desconocido"),
                    movement(employer_text="")):
            plan = group_applications([row], EMPLOYERS)
            self.assertEqual(plan["issues"], [])
            self.assertEqual(plan["groups"][0]["employer"], "NO IDENTIFICADA")
            self.assertEqual(plan["groups"][0]["rows"][0]["employer_text"], row["employer_text"])

    def test_unknown_text_not_forced_into_default_company(self):
        plan = group_applications([movement(employer_text="Desconocido")], EMPLOYERS, "A")
        self.assertEqual(plan["issues"], [])
        self.assertEqual(plan["groups"][0]["employer"], "NO IDENTIFICADA")

    def test_each_unknown_movement_has_own_import_even_on_same_date(self):
        rows = [movement(source_row=number, employer_text=label)
                for number, label in [(2, "Desconocida"), (3, "Desconocida"), (4, ""), (5, "A"), (6, "A")]]
        plan = group_applications(rows, EMPLOYERS)
        unknown = [group for group in plan["groups"] if group["employer"] == "NO IDENTIFICADA"]
        self.assertEqual(len(unknown), 3)
        self.assertTrue(all(group["count"] == 1 for group in unknown))
        self.assertEqual(next(group["count"] for group in plan["groups"] if group["employer"] == "A"), 2)

    def test_portfolio_company_wins_over_unknown_conflicting_or_ambiguous_text(self):
        employers = deepcopy(EMPLOYERS)
        employers[1]["aliases"] = ["Planilla A"]
        for label in ("Desconocido", "A", "Planilla A", ""):
            row = movement(employer_text=label, portfolio_employer="B")
            result = group_applications([row], employers)
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["groups"][0]["employer"], "B")
            self.assertEqual(result["groups"][0]["rows"][0]["employer_text"], label)

    def test_exact_name_takes_precedence_over_another_company_alias(self):
        employers = deepcopy(EMPLOYERS)
        employers[1]["aliases"] = ["Empresa A"]
        result = group_applications([movement(employer_text=" Empresa  A ")], employers)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["groups"][0]["employer"], "A")

    def test_unavailable_portfolio_company_cannot_fall_back_to_another(self):
        self.assertTrue(group_applications([movement(portfolio_employer="No disponible")], EMPLOYERS)["issues"])

    def test_8008_portfolio_resolved_rows_do_not_require_8008_aliases(self):
        rows = [movement(source_row=i + 2, loan_number=f"{i + 1}-1",
                         portfolio_employer="A", employer_text=f"Texto contable {i}")
                for i in range(8008)]
        result = group_applications(rows, EMPLOYERS)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["groups"][0]["count"], 8008)
        self.assertEqual(result["groups"][0]["total_usd"], 200200)

    def test_fallback_does_not_override_company_found_in_file_or_portfolio(self):
        for row in (movement(employer_text="B"), movement(employer_text="", portfolio_employer="B")):
            self.assertTrue(group_applications([row], EMPLOYERS, "A")["issues"])
        self.assertEqual(group_applications([movement(employer_text="")], EMPLOYERS, "A")["groups"][0]["employer"], "A")

    def test_ambiguous_alias_blocks_import(self):
        employers = deepcopy(EMPLOYERS)
        employers[1]["aliases"] = ["Planilla A"]
        self.assertTrue(group_applications([movement(employer_text="Planilla A")], employers)["issues"])

    def test_excludes_accounting_deposits(self):
        plan = group_applications([movement(event_type="Deposito"), movement()], EMPLOYERS)
        self.assertEqual(len(plan["excluded"]), 1)
        self.assertEqual(plan["groups"][0]["count"], 1)

    def test_duplicates_are_detected_within_file_and_against_prior_imports(self):
        row = movement()
        plan = group_applications([row, dict(row, source_row=5)], EMPLOYERS)
        self.assertEqual(len(plan["duplicates"]), 1)
        self.assertEqual(plan["groups"][0]["count"], 2)
        plan = group_applications([row], EMPLOYERS, existing={("A", movement_key(row))})
        self.assertEqual(plan["groups"][0]["count"], 1)
        self.assertEqual(len(plan["duplicates"]), 1)
        # Another accounting entry is not silently discarded as a duplicate.
        self.assertNotEqual(movement_key(row), movement_key(movement(voucher="AS-2")))

    def test_duplicate_from_other_employer_does_not_hide_a_movement(self):
        row = movement()
        plan = group_applications([row], EMPLOYERS, existing={("B", movement_key(row))})
        self.assertEqual(plan["groups"][0]["count"], 1)

    def test_nio_conversion_rounds_per_row_before_total(self):
        records = [movement(amount=824.78), movement(amount=824.78, loan_number="13376-1")]
        apply_accounting_currency_override(records, "NIO", "36.6243")
        group = group_applications(records, EMPLOYERS)["groups"][0]
        self.assertEqual(float(group["total_usd"]), 45.04)
        self.assertEqual(group["rows"][0]["amount_nio"], 824.78)

    def test_portfolio_identity_conflict_is_not_silently_imported(self):
        row = movement(portfolio_validation_status="Crédito duplicado en corte")
        self.assertEqual(len(group_applications([row], EMPLOYERS)["issues"]), 1)


if __name__ == "__main__":
    unittest.main()
