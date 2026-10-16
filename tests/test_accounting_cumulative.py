from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation import accounting_cumulative as cumulative
from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation import accounting_cumulative_review as review
from credinomina_reconciliation.parsers import SourceFileError


def movement(position=2, kind="Aplicacion", **changes):
    codes = {"Aplicacion": ("12", "05", "Aplicación de pago"), "Ajuste": ("01", "01", "Movimiento interno"),
             "Deposito": ("02", "12", "Depósito")}
    tmov, tdoc, classification = codes[kind]
    return {"source_row": position, "event_type": kind, "event_date": "2026-10-15", "source_account": "160209013004",
            "source_currency": "USD", "source_debit": 100 if kind == "Aplicacion" else 0,
            "source_credit": 0 if kind == "Aplicacion" else 100, "source_fx_rate": 0,
            "source_description": "Evidencia del core", "voucher": "00101739", "accounting_reference": "REF",
            "tmov": tmov, "tdoc": tdoc, "accounting_classification": classification,
            "accounting_source_key": f"new-{position}", "loan_number": "11762-1", "employer_text": "EMP",
            "amount_usd": 100, "amount": 100, "currency": "USD", "bank_deposit_reference": "REF", **changes}


def origin(position=2, kind="Aplicacion", **changes):
    doctype = {"Aplicacion": cumulative.IMPORT, "Ajuste": cumulative.COMPLEMENT, "Deposito": cumulative.DEPOSIT}[kind]
    return {**movement(position, kind), "source_file_hash": "old", "accounting_source_key": f"old-{position}",
            "doctype": doctype, "document": f"DOC-{position}", "employer": "EMP", **changes}


@pytest.mark.parametrize("kind", ["Aplicacion", "Ajuste", "Deposito"])
def test_extended_export_keeps_existing_ids_and_only_imports_additional_occurrences(kind):
    old = [origin(2, kind, allocation_detail='[{"importe_usd":100}]', result="Conciliado"), origin(3, kind)]
    before = deepcopy(old)
    result = cumulative.compare([movement(n, kind) for n in (9, 20, 50)], [movement(n, kind) for n in (2, 3)], old, "new")
    assert len(result["records"]) == 1
    assert result["records"][0]["source_row"] == 50
    assert len(result["already_imported"]) == 2
    assert not result["missing_origins"]
    assert old == before, "Preserve the original cash, allocation and financial state"


def test_absent_movements_of_all_types_remain_and_previous_file_defines_scope():
    old = [origin(2), origin(3, "Ajuste"), origin(4, "Deposito"), origin(5, voucher="OUTSIDE")]
    before = deepcopy(old)
    result = cumulative.compare([movement(9, voucher="NEW")], [movement(2), movement(3, "Ajuste"), movement(4, "Deposito")], old, "new")
    assert {row["document"] for row in result["missing_origins"]} == {"DOC-2", "DOC-3", "DOC-4"}
    assert len(result["records"]) == 1
    assert old == before


def test_reduced_multiplicity_reports_ambiguity_instead_of_claiming_exact_annulment():
    result = cumulative.compare([movement(9)], [movement(2), movement(3)], [origin(2), origin(3)], "new")
    assert not result["records"]
    assert len(result["missing_origins"]) == 1
    assert "candidato" in result["missing_origins"][0]["reason"]


@pytest.mark.parametrize("changes", [{"source_debit": 101}, {"loan_number": "999-1"}, {"voucher": "OTHER"},
                                     {"accounting_reference": "OTHER"}, {"source_description": "Otro"}])
def test_changed_evidence_is_not_silently_overwritten(changes):
    result = cumulative.compare([movement(9, **changes)], [movement(2)], [origin(2)], "new")
    assert len(result["records"]) == len(result["missing_origins"]) == 1


def test_changed_fx_is_blocked_instead_of_silently_ignoring_conversion():
    result = cumulative.compare([movement(source_fx_rate=36.6)], [movement()], [origin(source_fx_rate=36.5)], "new")
    assert not result["records"] and len(result["issues"]) == 1


def test_retry_after_later_occurrence_committed_reserves_exact_physical_row():
    result = cumulative.compare([movement(2), movement(3), movement(4)], [movement(2)],
        [origin(2), origin(4, source_file_hash="new", accounting_source_key="new-4")], "new")
    assert [row["source_row"] for row in result["records"]] == [3]
    assert {row["row"] for row in result["already_imported"]} == {2, 4}


def test_next_cumulative_export_matches_origins_from_multiple_older_files():
    result = cumulative.compare([movement(50), movement(60), movement(70)], [movement(8), movement(20)],
        [origin(2), origin(20, source_file_hash="second")], "third")
    assert [row["source_row"] for row in result["records"]] == [70]


def test_guard_stops_an_overlapping_different_file_between_committed_blocks():
    row = cumulative.compare([movement(2)], [], [], "new")["records"][0]
    plan = {"groups": [{"rows": [row]}], "file_hash": "new"}
    with patch.object(cumulative, "live_origins", return_value=[origin(8)]):
        with pytest.raises(SourceFileError, match="Otra carga"):
            cumulative.guard_block(plan)
    with patch.object(cumulative, "live_origins", return_value=[origin(2, source_file_hash="new")]):
        cumulative.guard_block(plan)


def test_live_origins_deduplicates_complement_deposit_sources_and_parent_mirrors():
    item = frappe._dict(origin(2, "Ajuste", name="ITEM", source_date="2026-10-15", source_voucher="00101739"))
    source = frappe._dict({**item, "name": "CHILD", "parent": "MANUAL", "source_loan_number": "11762-1"})
    deposit = frappe._dict({**source, "name": "MANUAL"})
    def query(doctype, **kwargs):
        return {"CN Source Row": [], cumulative.COMPLEMENT: [item], "CN Deposit Accounting Source": [source],
                cumulative.DEPOSIT: [deposit]}[doctype]
    with patch.object(frappe, "get_all", side_effect=query), patch.object(frappe, "get_list", side_effect=lambda _, **kw: kw["filters"]["name"][1]):
        result = cumulative.live_origins([movement(2, "Ajuste")])
    assert len(result) == 1
    assert result[0]["document"] == "ITEM"


def test_hidden_matching_evidence_cannot_be_imported_again():
    item = frappe._dict(origin(2, "Ajuste", name="ITEM", source_date="2026-10-15", source_voucher="00101739"))
    with patch.object(frappe, "get_all", side_effect=lambda doctype, **kw: [item] if doctype == cumulative.COMPLEMENT else []), \
         patch.object(frappe, "get_list", return_value=[]), patch.object(frappe, "throw", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            cumulative.live_origins([movement(2, "Ajuste")])


def test_real_bulk_plan_filters_all_three_types_before_creation():
    previous = [movement(2), movement(3, "Ajuste"), movement(4, "Deposito")]
    current = previous + [movement(10, voucher="NEW"), movement(11, "Ajuste", voucher="NEW"), movement(12, "Deposito", voucher="NEW")]
    snapshot = [origin(2), origin(3, "Ajuste"), origin(4, "Deposito")]
    with patch.object(bulk, "_permissions"), patch.object(bulk, "_required_portfolio_snapshot", return_value="CUT"), \
         patch.object(bulk, "_file", side_effect=lambda file: (frappe._dict(file_name=file), file.encode())), \
         patch.object(bulk, "parse_accounting_movements", side_effect=lambda name, _, **kw: deepcopy(current if name == "new.csv" else previous)), \
         patch.object(bulk, "enrich_accounting_records", side_effect=lambda rows, *args, **kw: rows), \
         patch.object(bulk, "attach_employer_aliases"), patch.object(bulk, "_validate_portfolio_companies"), \
         patch.object(frappe, "get_list", return_value=[frappe._dict(name="EMP", employer_name="EMP", employer_code="EMP")]), \
         patch.object(frappe, "get_all", return_value=[]), patch.object(frappe, "has_permission", return_value=True), \
         patch.object(cumulative, "live_origins", return_value=snapshot):
        plan = bulk._plan({"source_file": "new.csv", "previous_source_file": "old.csv", "cumulative_import": 1,
                           "currency": "USD", "portfolio_snapshot": "CUT"})
    assert not plan["issues"]
    assert [row["source_row"] for group in plan["groups"] for row in group["rows"]] == [10]
    assert [row["source_row"] for row in plan["complementary"]] == [11]
    assert [row["source_row"] for row in plan["deposits"]] == [12]
    assert len(plan["already_imported"]) == 3
    assert all("_cumulative_baseline" in row for row in plan["complementary"] + plan["deposits"])


def test_full_absence_report_is_durable_beyond_first_hundred_rows():
    rows = [{**cumulative.reference(origin(n)), "reason": "Revisar"} for n in range(620)]
    payloads = {}
    def save(_, name, **values):
        payloads[name] = values["payload"]
    with patch.object(frappe, "db", Mock(get_value=Mock(side_effect=lambda _, name, *args: payloads.get(name)))), \
         patch.object(review, "insert_internal", side_effect=save):
        assert review.save_missing("batch", rows) == 3
        assert review.save_missing("batch", rows) == 3
    import json
    assert sum(len(json.loads(blob)) for blob in payloads.values()) == 620


def test_monetary_fingerprint_is_stable_across_database_decimals():
    assert cumulative.fingerprint(movement(source_debit=100.0)) == cumulative.fingerprint(movement(source_debit=Decimal("100.00")))


def test_empty_cumulative_export_reports_all_origins_but_still_requires_valid_header():
    from credinomina_reconciliation.parsers import parse_accounting_movements
    header = b"Fecha Aplica,Cuenta Contable,Debito del Mes,Credito del Mes\n"
    assert parse_accounting_movements("empty.csv", header, allow_empty=True) == []
    with pytest.raises(SourceFileError):
        parse_accounting_movements("empty.csv", header)
    with pytest.raises(SourceFileError):
        parse_accounting_movements("bad.csv", b"Wrong header\n", allow_empty=True)
    result = cumulative.compare([], [movement(2)], [origin(2)], "new")
    assert len(result["missing_origins"]) == 1 and not result["records"]


def test_csv_download_uses_saved_complete_evidence_and_checks_owner_and_documents():
    import csv
    import io
    import json
    rows = [{**cumulative.reference(origin(n)), "description": "=SUM(1,2)", "reason": "Revisar"} for n in range(620)]
    blobs = {f"batch-missing-{i // 250 + 1}": json.dumps(rows[i:i + 250]) for i in range(0, len(rows), 250)}
    response = frappe._dict()
    document = Mock()
    with patch.object(bulk, "_state", return_value={"missing_chunks": 3}) as state, \
         patch.object(frappe.db, "get_value", side_effect=lambda _, name, *args: blobs.get(name)), \
         patch.object(frappe, "get_doc", return_value=document), patch.object(frappe.local, "response", response):
        review.download_missing_origins("batch")
    state.assert_called_once_with("batch")
    assert document.check_permission.call_count == 620
    data = list(csv.reader(io.StringIO(response.filecontent.decode("utf-8-sig"))))
    assert len(data) == 621
    assert all(row[-2] == "'=SUM(1,2)" for row in data[1:])
    assert response.type == "download"


def test_csv_download_rejects_foreign_batch_before_reading_evidence():
    with patch.object(bulk, "_state", side_effect=frappe.PermissionError), patch.object(frappe.db, "get_value") as query:
        with pytest.raises(frappe.PermissionError):
            review.download_missing_origins("foreign")
        query.assert_not_called()
