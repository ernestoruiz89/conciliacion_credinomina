"""Real saved fields and report queries, rollback-only on the isolated test site."""
import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "CTL-" + frappe.generate_hash(length=8)
    description = "REGISTRAMOS RELASIFICACION DE SALDOS A FAVOR DE LA CUENTA DE CONVENIO A LA 3001, DE LOS MESES DE NOVIEMBRE A DICIEMBRE 2025, SEGUN DETALLE-INGRIS MASSIEL HERNANDEZ AGROSACO 2426.73\n" + "Descripción completa " * 200
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
        item = frappe.get_doc({"doctype": "CN Complementary Item", "category": "Por clasificar", "posting_date": "2025-04-15",
            "source_date": "2025-04-15", "currency": "USD", "amount": 50, "source_account": marker,
            "source_currency": "NIO", "source_debit": 0, "source_credit": 1831.22, "source_fx_rate": 36.6243,
            "source_description": description, "description": "Observación distinta de la evidencia",
            "accounting_source_key": marker + "-credit", "source_file": "/private/files/test.csv"}).insert()
        document = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer.name,
            "source_file": "/private/files/test.csv", "currency": "NIO", "manual_fx_rate": 36.6243, "status": "Importado"})
        for event, amount, debit, credit, key in [("Aplicacion", 100, 3662.43, 0, "-debit"), ("Ajuste", 50, 0, 1831.22, "-credit")]:
            document.append("rows", {"event_type": event, "event_date": "2025-04-15", "source_key": marker + key,
                "accounting_source_key": marker + key, "currency": "USD", "amount": amount, "amount_usd": amount,
                "source_currency": "NIO", "source_account": marker, "source_debit": debit, "source_credit": credit,
                "source_description": description, "description": "Descripción normalizada", "source_fx_rate": 36.6243,
                "manual_fx_rate": 36.6243, "effective": int(event == "Aplicacion"), "client_name": "INGRIS MASSIEL HERNANDEZ",
                "client_number": marker, "loan_number": "12345-1", "complementary_item": item.name if event == "Ajuste" else ""})
        document.insert()
        columns, rows, message, chart, totals = execute({"month": "2025-04-19", "source_account": marker})
        assert len(rows) == 2, rows
        assert all(row["description"] == description for row in rows)
        assert sum(row["debit_usd"] for row in rows) == 100
        assert sum(row["credit_usd"] for row in rows) == 50
        assert all(row["accounting_import"] == document.name for row in rows)
        assert any(row["complementary_item"] == item.name for row in rows)
        _, summary, *_ = execute({"month": "2025-04-01", "source_account": marker, "summary": 1})
        assert len(summary) == 1 and summary[0]["movement_count"] == 2
        assert summary[0]["net_usd"] == 50 and summary[0]["net_nio"] == 1831.21
        # Bulk-only nonpayment evidence must appear even with no employer/import link.
        standalone = frappe.get_doc({"doctype": "CN Complementary Item", "category": "Por clasificar", "posting_date": "2025-04-30",
            "source_date": "2025-04-30", "currency": "USD", "amount": 10, "source_account": marker,
            "source_currency": "USD", "source_debit": 0, "source_credit": 10,
            "source_description": description, "description": "Movimiento sin convenio", "source_client_name": "Nombre original",
            "accounting_source_key": marker + "-bulk"}).insert()
        _, rows, *_ = execute({"month": "2025-04-01", "source_account": marker})
        assert len(rows) == 3 and sum(row["credit_usd"] for row in rows) == 60
        bulk = next(row for row in rows if row["complementary_item"] == standalone.name)
        assert not bulk["employer"] and not bulk["accounting_import"] and bulk["client_name"] == "Nombre original"
        assert bulk["credit_nio"] is None
        standalone.review_action = "No conciliatoria"
        standalone.review_notes = "No representa pago de cliente"
        standalone.save()
        _, rows, *_ = execute({"month": "2025-04-01", "source_account": marker})
        assert next(row for row in rows if row["complementary_item"] == standalone.name)["state"] == "No conciliatoria"
        assert sum(row["credit_usd"] for row in rows) == 60  # original turnover is unchanged
        # New filters match the displayed values, not the source import's status.
        base = {"month": "2025-04-01", "source_account": marker}
        for filters, expected in [({"movement_type": "Aplicacion"}, 1), ({"state": "No conciliatoria"}, 1),
                                  ({"movement_type": "Aplicacion", "state": "No conciliatoria"}, 0),
                                  ({"movement_type": "", "state": ""}, 3)]:
            _, selected, _, _, kpis = execute({**base, **filters})
            assert len(selected) == expected, (filters, selected)
            assert next(kpi["value"] for kpi in kpis if kpi["label"] == "Movimientos") == expected
            assert next(kpi["value"] for kpi in kpis if kpi["label"] == "Créditos US$") == sum(row["credit_usd"] or 0 for row in selected)
        _, selected, _, _, kpis = execute({**base, "state": "No conciliatoria", "summary": 1})
        assert len(selected) == 1 and selected[0]["movement_count"] == 1 and selected[0]["credit_usd"] == 10
        assert selected[0]["source_currency"] == "USD"
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": employer.name,
            "deposit_date": "2025-06-10", "deposit_reference": marker, "deposit_currency": "USD", "deposit_amount": 7,
            "source_date": "2025-05-01", "source_account": marker, "source_currency": "USD", "source_credit": 7,
            "accounting_source_key": marker + "-deposit", "accounting_classification": "Depósito"}).insert()
        span = {"from_date": "2025-04-30", "to_date": "2025-05-01", "source_account": marker}
        _, selected, _, _, kpis = execute(span)
        assert len(selected) == 2 and {row["event_date"] for row in selected} == {"2025-04-30", "2025-05-01"}
        assert any(row["remittance_allocation"] == deposit.name for row in selected)
        assert sum(row["credit_usd"] for row in selected) == 17
        _, grouped, *_ = execute({**span, "summary": 1})
        assert len(grouped) == 2 and {row["month"] for row in grouped} == {"2025-04", "2025-05"}
        _, same_day, *_ = execute({**span, "from_date": "2025-05-01"})
        assert len(same_day) == 1 and same_day[0]["remittance_allocation"] == deposit.name
        _, no_rows, *_ = execute({**span, "from_date": "2025-05-02", "to_date": "2025-05-03"})
        assert no_rows == []
        # Data backfill is idempotent and does not overwrite existing full descriptions.
        from unittest.mock import patch
        from credinomina_reconciliation.patches.v1_0.add_accounting_control_report import execute as backfill
        original_amount = document.rows[0].amount
        frappe.db.set_value("CN Source Row", document.rows[0].name, {"source_description": "", "source_fx_rate": 0})
        with patch.object(frappe, "reload_doc"), patch("credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow.execute"):
            backfill()
            backfill()
        document.reload()
        assert document.rows[0].source_description == "Descripción normalizada"
        assert document.rows[0].source_fx_rate == 36.6243 and document.rows[0].amount == original_amount
        assert document.rows[1].source_description == description
        return {"full_description": "OK", "mirror_counted_once": "OK", "nio_usd_totals": "OK",
                "standalone_unidentified": "OK", "classification_preserves_totals": "OK", "idempotent_backfill": "OK",
                "movement_and_state_filters_details_summary_totals": "OK", "inclusive_date_range_and_monthly_summary": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
