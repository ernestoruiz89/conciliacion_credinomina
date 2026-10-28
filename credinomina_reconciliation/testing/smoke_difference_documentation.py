"""Rollback-only real SQL checks of documentary writes and legacy migration."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.difference_documentation import document_difference
from credinomina_reconciliation.patches.v1_0.document_differences_preserve_manual_items import mark_existing_items


def snapshot(doctype, name, ignored=()):
    values = frappe.db.get_value(doctype, name, "*", as_dict=True)
    return {key: value for key, value in values.items() if key not in ignored}


def check_no_new_tolerances(marker):
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.rounding import money
    for index, (applied, paid) in enumerate(((46.52, 46.53), (46.53, 46.52))):
        label = f"{marker}-T{index}"
        company = frappe.get_doc(dict(doctype="CN Employer", employer_name=label, employer_code=label,
                                     rounding_tolerance_usd=.10)).insert()
        period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
            payroll_month="2025-04-01", reconciliation_mode="Historica")).insert()
        imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name, status="Importado",
            source_file=f"/private/files/{label}.csv", historical_backfill=1, historical_period=period.name,
            rows=[dict(source_row=2, source_key=label, event_type="Aplicacion", event_date="2025-04-30",
                client_name=label, client_number=label, loan_number=label, reference=label, currency="USD",
                amount=applied, amount_usd=applied, processing_route="Historica", historical_period=period.name,
                effective=1, match_status="Pendiente")])).insert()
        _reconcile_sources(company.name, preserve_deposits=True)
        imported.reload()
        deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
            deposit_reference=label, deposit_date="2025-05-10", deposit_currency="USD", deposit_amount=paid,
            detail_periods=[dict(period=period.name)], targets=[dict(historical_application=imported.rows[0].name,
                amount_usd=min(applied, paid), notes="Distribución explícita de prueba")])).insert()
        deposit.submit()
        reconcile_deposit(deposit)
        imported.reload()
        deposit.reload()
        assert not frappe.db.exists("CN Complementary Item", {"employer": company.name, "category": "Diferencia por tolerancia"})
        assert money(imported.rows[0].historical_balance_usd) == max(money(applied)-money(paid), 0), imported.rows[0].as_dict()
        assert money(deposit.unallocated_usd) == max(money(paid)-money(applied), 0), deposit.as_dict()
        assert not imported.rows[0].rounding_adjustment_usd
        # Simulate prior production evidence; reprocesing must not overwrite it.
        old = frappe.get_doc(dict(doctype="CN Complementary Item", name=label, docstatus=1,
            category="Diferencia por tolerancia", status="Vigente", movement_key=label,
            employer=company.name, period=period.name, deposit_source_row=deposit.name,
            application_source_row=imported.rows[0].name, signed_amount_usd=.01, amount_usd=.01))
        old.db_insert()
        before = snapshot(deposit.doctype, deposit.name)
        try:
            reconcile_deposit(deposit)
        except frappe.ValidationError as error:
            assert "tolerancias históricas" in str(error)
        else:
            raise AssertionError("Se reprocesó una tolerancia histórica sin revisión")
        assert snapshot(deposit.doctype, deposit.name) == before


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    tables = ("CN Employer", "CN Reconciliation Period", "CN Accounting Import", "CN Source Row", "CN Complementary Item", "CN Remittance Allocation", "CN Remittance Target")
    counts = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            marker = "DIFF-" + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=company.name,
                payroll_month="2026-08-01", reconciliation_mode="Historica")).insert()
            frappe.db.set_value(period.doctype, period.name, {"status": "Cerrado", "applied_total_usd": 100,
                                "remitted_total_usd": 100}, update_modified=False)
            closed_before = snapshot(period.doctype, period.name)
            imported = frappe.get_doc(dict(doctype="CN Accounting Import", employer=company.name,
                currency="USD", status="Importado", source_file=f"/private/files/{marker}.csv",
                rows=[dict(event_type="Aplicacion", event_date="2026-10-15", currency="USD", amount=100,
                    amount_usd=100, effective=1, source_key=marker, client_number=marker,
                    client_name="Cliente de ensayo", loan_number="1-1")])).insert()
            source = imported.rows[0]
            # Historical fixture bypasses current policy only to simulate production before migration.
            item = frappe.get_doc(dict(doctype="CN Complementary Item", name=marker, docstatus=1,
                category="Ajuste de aplicación", employer=company.name, posting_date="2026-10-15",
                period=period.name,
                amount=10, amount_usd=10, currency="USD", related_import=imported.name,
                related_application=source.name, application_adjustment_usd=10))
            item.db_insert()
            deposit = frappe.get_doc(dict(doctype="CN Remittance Allocation", name=marker,
                employer=company.name, docstatus=1, deposit_amount=90, amount_usd=90,
                deposit_currency="USD", deposit_date="2026-10-15", deposit_reference=marker,
                result="Conciliado", allocated_usd=90, allocation_detail='[]'))
            deposit.db_insert()
            target = frappe.get_doc(dict(doctype="CN Remittance Target", name=marker, parent=deposit.name,
                parenttype=deposit.doctype, parentfield="targets", idx=1, docstatus=1,
                complementary_item=item.name, amount_usd=10, result="Aplicada"))
            target.db_insert()
            before = {kind: snapshot(kind, name, ("legacy_manual_review",)) for kind, name in
                      ((item.doctype, item.name), (deposit.doctype, deposit.name), (target.doctype, target.name))}
            mark_existing_items()
            mark_existing_items()
            assert snapshot(period.doctype, period.name) == closed_before
            for kind, name in ((item.doctype, item.name), (deposit.doctype, deposit.name), (target.doctype, target.name)):
                assert snapshot(kind, name, ("legacy_manual_review",)) == before[kind], kind
            assert frappe.db.get_value(item.doctype, item.name, "legacy_manual_review") == 1
            reason = frappe.get_all("CN Difference Reason", pluck="name", limit_page_length=1)[0]
            from credinomina_reconciliation.accounting_review import create_review_items
            record = dict(accounting_source_key=marker + "-CORE", resolved_employer=company.name,
                event_date="2026-10-15", amount_usd=10, source_currency="USD", source_credit=10,
                source_debit=0, voucher=marker + "-V", classification_reason="Movimiento interno de ensayo",
                accounting_classification="Movimiento interno", source_row=2)
            with patch("credinomina_reconciliation.file_references.attach_existing_file"):
                core = create_review_items([record], imported.source_file, marker)[0]
                assert core.accounting_source_key == marker + "-CORE"
                assert not create_review_items([record], imported.source_file, marker)
            for document, field, row in ((imported, "rows", source), (deposit, "", deposit), (item, "", item)):
                document.reload()
                ignored = ("modified", "modified_by", "_comments", "difference_reason", "difference_notes")
                parent_before, row_before = snapshot(document.doctype, document.name, ignored), snapshot(row.doctype, row.name, ignored)
                document_difference(document.doctype, document.name, str(document.modified), reason,
                                    "Diferencia explicada, pendiente de soporte del core", field, row.name if field else "")
                assert snapshot(document.doctype, document.name, ignored) == parent_before
                assert snapshot(row.doctype, row.name, ignored) == row_before
                assert frappe.db.get_value(row.doctype, row.name, "difference_reason") == reason
            # Stale requests, unauthorized writers and wrong child scopes never change balances/notes.
            imported.reload()
            immutable = snapshot(source.doctype, source.name)
            for changes in ({"modified": "stale"}, {"row_name": "another-row"}, {"difference_reason": "missing-reason"}):
                args = dict(doctype=imported.doctype, name=imported.name, modified=str(imported.modified),
                    difference_reason=reason, difference_notes="Other", parentfield="rows", row_name=source.name)
                args.update(changes)
                try:
                    document_difference(**args)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError("Se aceptó documentación con alcance o versión inválidos")
                assert snapshot(source.doctype, source.name) == immutable
            with patch.object(type(imported), "check_permission", side_effect=frappe.PermissionError):
                try:
                    document_difference(imported.doctype, imported.name, str(imported.modified), reason, "Other", "rows", source.name)
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError("No se comprobó permiso de escritura")
            assert snapshot(source.doctype, source.name) == immutable
            report_before = snapshot(deposit.doctype, deposit.name)
            report = frappe.get_doc("Report", "Revision de Partidas Manuales")
            _columns, summary, _message = report.execute_module({"item": item.name, "include_tolerance": 1})
            assert len(summary) == 1 and summary[0]["item"] == item.name, summary
            assert not any(column["fieldname"] in {"relation", "details", "link_count"} for column in _columns)
            assert snapshot(deposit.doctype, deposit.name) == report_before
            assert snapshot(period.doctype, period.name) == closed_before
            try:
                frappe.get_doc(dict(doctype="CN Complementary Item", employer=company.name,
                    category="Otros ingresos", currency="USD", amount=5, reference=marker)).insert()
            except frappe.ValidationError as error:
                assert "partidas nuevas" in str(error)
            else:
                raise AssertionError("Se permitió crear una partida manual")
            check_no_new_tolerances(marker)
            return dict(documentation_does_not_change_financial_fields=True, legacy_links_preserved=True,
                        positive_and_negative_cent_not_absorbed=True, legacy_tolerance_reprocessing_preserved=True,
                        patch_idempotent=True, report_inventory_only=True, manual_creation_blocked=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {name: frappe.db.count(name) for name in tables}
