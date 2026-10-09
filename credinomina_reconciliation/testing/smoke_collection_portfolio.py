"""Import unnamed collections against actual monthly cuts; disposable site only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.collection_portfolio import enrich_collection_records
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_api,
)
from credinomina_reconciliation.parsers import SourceFileError


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        marker = "COL-PORT-" + frappe.generate_hash(length=8)
        employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
            employer_code=marker, payroll_frequency="Mensual")).insert()
        cuts = []
        for month, day in ((8, 31), (9, 30), (10, 31)):
            cuts.append(frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot",
                source_file=f"/private/files/{marker}-{month}.xlsx", status="Importado",
                report_date=f"2094-{month:02d}-{day}", rows=[dict(
                    credit_number=loan, client_name=f"Cliente {index} corte {month}",
                    client_number_core=f"{marker}-{index}", national_id=f"ID-{marker}-{index}",
                    employer=employer.name, employer_text=employer.name,
                    employer_match_status="Empresa identificada",
                    credit_lifecycle="Activo") for index, loan in enumerate(("0012800-1", "13519-1"), 1)]
            )).insert())
        period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
            payroll_month="2094-09-01", reconciliation_mode="Operativa", collection_cycle="Fecha exacta",
            cutoff_date="2094-09-15", application_basis="Cobranza")).insert()
        content = "Nro. Crédito,Monto de la cuota en US$\n12800,79.23\n13519-1,23.06\n".encode()
        with patch.object(period_api, "_attached_file", return_value=(
                frappe._dict(file_name="cobranza.csv"), content)), \
             patch.object(period_api, "_reconcile_if_sources", return_value=None):
            result = period_api.import_collection(period.name)
            assert result["portfolio"]["snapshot"] == cuts[1].name, result
            assert result["portfolio"]["completed_rows"] == 2, result
            period.reload()
            assert [row.client_name for row in period.collection_rows] == ["Cliente 1 corte 9", "Cliente 2 corte 9"]
            assert all(row.client and row.national_id for row in period.collection_rows)
            assert not frappe.get_meta("CN Collection Row").has_field("client_number")
            assert [row.expected_usd for row in period.collection_rows] == [79.23, 23.06]
            assert cuts[1].name in period.notes
            assert period_api.import_collection(period.name)["unchanged"]

            # A legacy row with a blank number has an old identity hash and links.
            row = period.collection_rows[0]
            legacy = dict(row.as_dict(), client="", client_number="", national_id="")
            legacy_key = period_api._collection_identity_key(period, legacy)
            frappe.db.set_value("CN Collection Row", row.name, dict(client="", national_id="",
                row_key=legacy_key, applied_usd=79.23, remitted_usd=79.23,
                application_reference="KEEP-REFERENCE", first_exception_comment="KEEP-COMMENT"))
            result = period_api.import_collection(period.name)
            assert not result.get("unchanged"), result
            period.reload()
            restored = period.collection_rows[0]
            assert (restored.name, restored.row_key) == (row.name, legacy_key)
            assert restored.client == f"{marker}-1" and restored.national_id
            assert (restored.applied_usd, restored.remitted_usd) == (79.23, 79.23)
            assert restored.application_reference == "KEEP-REFERENCE"
            assert restored.first_exception_comment == "KEEP-COMMENT"
            assert period_api.import_collection(period.name)["unchanged"]

            # Ordinary saves cannot discard the sole client identity.
            period.reload()
            period.collection_rows[0].client = ""
            try:
                period.save()
            except frappe.ValidationError as exc:
                assert "identifique" in str(exc), exc
            else:
                raise AssertionError("The collection lost its client link")
            period.reload()
            assert period.collection_rows[0].client == f"{marker}-1"

        frappe.db.set_value("CN Credit Portfolio Snapshot", cuts[1].name, "disabled", 1)
        record = dict(source_row=2, loan_number="12800")
        result = enrich_collection_records([record], employer.name, "2094-09-15")
        assert result["snapshot"] == cuts[0].name and record["client_name"] == "Cliente 1 corte 8"
        # An October-only credit must not use next month's identity.
        frappe.db.set_value("CN Credit Portfolio Row", cuts[2].rows[0].name, "credit_number", "999999-1")
        try:
            enrich_collection_records([dict(source_row=2, loan_number="999999-1")], employer.name, "2094-09-15")
        except SourceFileError:
            pass
        else:
            raise AssertionError("A future-month portfolio was used")
        return dict(unnamed_import=True, month_end_for_midmonth=True, earlier_month_fallback=True,
                    future_month_excluded=True, reimport_preserves_links=True,
                    sole_client_link=True, unresolved_client_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
