"""Rollback-only accounting follow-up, including cent adjustment and NIO ledger."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_exceptions as api
from credinomina_reconciliation.accounting_control import build_rows, summarize
from credinomina_reconciliation.tolerance_items import tolerance_item_write
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute as accounting_report


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "CORE-" + frappe.generate_hash(length=8)
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker, "payroll_frequency": "Mensual"}).insert()
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                "payroll_month": "2025-09-01", "reconciliation_mode": "Historica",
                "historical_scope": "Fecha exacta", "historical_application_date": "2025-09-30"}).insert()
            period.db_set("status", "Cerrado", update_modified=False)
            before = frappe.db.get_value(period.doctype, period.name, "modified")
            origin = frappe.get_doc({"doctype": api.ITEM, "category": "Ajuste de conciliación",
                "subcategory": "Otro ajuste sin CxC",
                "employer": employer.name, "period": period.name, "reference": marker,
                "posting_date": "2025-10-01", "currency": "USD", "amount": -0.01,
                "description": "Centavo para registrar en el core"}).insert()
            origin.flags.defer_reconciliation = True
            origin.submit()
            name = api.create_item_exception(origin.name, "Administrator", "2026-10-05")
            assert api.create_item_exception(origin.name, "Administrator", "2026-10-06") == name
            exception = frappe.get_doc(api.EXCEPTION, name)
            origin.reload()
            assert origin.accounting_exception == name and origin.accounting_status == "Pendiente de registro"
            assert exception.origin_period == period.name and not exception.period
            assert exception.next_action == api.ACTION and len(exception.follow_up_actions) == 1
            exception.status = "Resuelta"
            exception.core_voucher = marker + "-AS"
            exception.resolution = "Escribir asiento no es verificar"
            try:
                exception.save()
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Resolved without imported evidence")
            # No internal/manual amount is counted in the external trial-balance report.
            rows, _ = build_rows([], {}, [origin.as_dict()])
            assert not rows
            ledger = frappe.get_doc({"doctype": api.ITEM, "category": "Por clasificar",
                "employer": employer.name, "posting_date": "2025-10-03", "currency": "USD", "amount": -0.01,
                "voucher": marker + "-AS", "voucher_line": marker + "-KEY",
                "review_action": "Pendiente de revisión", "accounting_source_key": marker + "-KEY",
                "source_voucher": marker + "-AS", "source_account": "3004", "source_currency": "NIO",
                "source_credit": 0.37, "source_debit": 0, "source_fx_rate": 36.6243,
                "source_file": f"/private/files/{marker}.csv", "source_file_hash": marker + "-HASH", "source_row": 2,
                "source_date": "2025-10-03", "source_description": "Registro real del centavo",
                "description": "Registro real del centavo"}).insert()
            candidates = api.get_registration_candidates(name, marker + "-AS")
            assert len(candidates) == 1 and candidates[0]["name"] == ledger.name, candidates
            api.verify_and_resolve(name, marker + "-AS", api.ITEM, ledger.name, "Ajuste registrado y verificado")
            exception.reload(); origin.reload(); ledger.reload()
            assert exception.status == "Resuelta" and origin.accounting_status == "Registrada"
            assert exception.core_verified_by == "Administrator" and exception.core_verified_on
            assert len(exception.follow_up_actions) == 2
            assert ledger.registration_exception == name and ledger.review_action == "No conciliatoria"
            original = ledger.as_dict()
            rows, _ = build_rows([], {}, [origin.as_dict(), original])
            totals = summarize(rows)
            assert len(rows) == 1 and totals[0]["credit_nio"] == 0.37 and totals[0]["credit_usd"] == 0.01, totals
            assert rows[0]["state"] == "Registro contable verificado"
            ledger.review_action = "Partida de depósito"
            try:
                ledger.save()
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Accounting evidence redistributed twice")
            assert frappe.db.get_value(period.doctype, period.name, "modified") == before
            # Automatic differences remain financially read-only but support the same workflow.
            with tolerance_item_write():
                automatic = frappe.get_doc({"doctype": api.ITEM, "category": "Diferencia por tolerancia",
                    "movement_key": marker + "-AUTO", "employer": employer.name, "period": period.name,
                    "status": "Vigente", "claim_id": "H:SMOKE", "posting_date": "2025-10-01", "signed_amount_usd": 0.01,
                    "tolerance_usd": 0.01, "reason": "Tolerancia interna"}).insert()
                automatic.submit()
            auto_exception = api.create_item_exception(automatic.name, "Administrator", "2026-10-05")
            automatic.reload()
            assert automatic.accounting_exception == auto_exception and automatic.accounting_status == "Pendiente de registro"
            with tolerance_item_write():
                automatic.save()
            assert automatic.accounting_status == "Pendiente de registro" and automatic.amount_usd == 0.01
            positive = frappe.get_doc({"doctype": api.ITEM, "category": "Por clasificar",
                "employer": employer.name, "posting_date": "2025-10-03", "currency": "USD", "amount": 0.01,
                "voucher": marker + "-POS", "voucher_line": marker + "-POS-KEY",
                "review_action": "Pendiente de revisión", "accounting_source_key": marker + "-POS-KEY",
                "source_voucher": marker + "-POS", "source_account": "3004", "source_currency": "USD",
                "source_debit": 0.01, "source_credit": 0, "source_file": f"/private/files/{marker}.csv",
                "source_row": 3, "source_date": "2025-10-03", "source_file_hash": marker + "-HASH",
                "source_description": "Registro tolerancia en el core", "description": "Registro tolerancia en el core"}).insert()
            source = frappe.get_doc({"doctype": api.IMPORT, "employer": employer.name,
                "source_file": f"/private/files/{marker}.csv", "file_hash": marker + "-HASH", "status": "Importado", "currency": "USD"})
            source.append("rows", {"event_type": "Ajuste", "event_date": "2025-10-03", "source_row": 3,
                "source_key": marker + "-ROW", "accounting_source_key": marker + "-POS-KEY",
                "complementary_item": positive.name, "currency": "USD", "amount": 0.01,
                "amount_usd": 0.01, "source_currency": "USD", "source_debit": 0.01,
                "source_credit": 0, "source_account": "3004", "voucher": marker + "-POS",
                "source_description": "Registro tolerancia en el core"})
            source.insert()
            candidates = api.get_registration_candidates(auto_exception, marker + "-POS")
            assert len(candidates) == 1 and candidates[0]["type"] == api.SOURCE, candidates
            api.verify_and_resolve(auto_exception, marker + "-POS", api.SOURCE, source.rows[0].name,
                "Registro del ajuste automático verificado")
            automatic.reload()
            with tolerance_item_write():
                automatic.save()
            assert automatic.accounting_status == "Registrada" and not automatic.voucher
            assert api.get_verified_document(auto_exception) == {"doctype": api.IMPORT, "name": source.name}
            _, report_rows, _, _, _ = accounting_report({"from_date": "2025-10-01", "to_date": "2025-10-31", "employer": employer.name})
            assert len(report_rows) == 2 and all(row["state"] == "Registro contable verificado" for row in report_rows), report_rows
            source.set("rows", [])
            try:
                source.save()
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Verified accounting evidence deleted by reimport")
            return {"ok": True, "cases": ["signed NIO cent verified", "direct links and history",
                "closed period unchanged", "manual amount excluded from turnover", "ledger counted once",
                "evidence cannot be redistributed", "automatic tolerance follow-up preserved", "import child and mirror verified once",
                "actual report status and totals", "verified source cannot disappear on reimport"]}
    finally:
        frappe.db.rollback()
