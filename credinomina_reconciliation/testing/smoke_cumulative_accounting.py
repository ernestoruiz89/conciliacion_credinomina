"""Cumulative ledger updates on the disposable site; always roll back."""
import csv
import io

import frappe

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.accounting_batch_store import creation_guard
from credinomina_reconciliation.accounting_cumulative import guard_block


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "CUM-" + frappe.generate_hash(length=8)
    tables = ["CN Accounting Import", "CN Source Row", "CN Complementary Item", "CN Remittance Allocation", "CN Deposit Accounting Source", "File"]
    counts = {table: frappe.db.count(table) for table in tables}
    try:
        company = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker, employer_code=marker)).insert()
        number = str(900000000000 + int(frappe.generate_hash(length=10), 16))
        bank = frappe.get_doc(dict(doctype="CN Bank Account", account_name=marker, bank_name="BAC",
                                  account_number=number, currency="USD", active=1)).insert()
        cut_date = next(f"{year}-01-15" for year in range(2090, 2200)
                        if not frappe.db.exists("CN Credit Portfolio Snapshot", {"cut_month": f"{year}-01", "disabled": 0}))
        portfolio = frappe.get_doc(dict(doctype="CN Credit Portfolio Snapshot", report_date=cut_date,
            source_file=f"/private/files/{marker}-portfolio.csv", status="Importado",
            rows=[dict(credit_number=marker + "-1", employer=company.name, client_name=marker, client_number_core=marker)])).insert()

        def receipt(ref):
            return frappe.get_doc(dict(doctype="CN Remittance Allocation", employer=company.name,
                bank_account=bank.name, deposit_date="2025-05-10", deposit_reference=ref,
                deposit_currency="USD", deposit_amount=100, apply_fifo=0)).insert().submit()

        manual = receipt(marker + "-BANK")
        second_manual = receipt(marker + "-NEW-BANK")

        def line(kind, suffix):
            loan = marker + "-1"
            description = marker + " " + suffix
            tmov, tdoc, debit, credit = ("12", "05", 50, 0) if kind == "A" else ("01", "01", 0, 20)
            ref = marker + "-" + suffix
            if kind == "D":
                tmov, tdoc, debit, credit, loan = "02", "12", 0, 100, ""
                description = f"US$100.00 DEPOSITO EN LA CUENTA BAC {number} US$ EL DIA 10/05/2025"
            return ["2025-05-15", "1602", description, tmov, tdoc, marker + "-" + suffix, ref, debit, credit, company.name, loan]

        def export(rows, label):
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["Fecha Aplica", "Cuenta Contable", "Descripción", "TMov", "TDoc", "No. Cmpte", "No. Ref",
                             "Débito del Mes", "Crédito del Mes", "Empresa", "No. Crédito"])
            writer.writerows(rows)
            return frappe.get_doc(dict(doctype="File", file_name=marker + label + ".csv", content=stream.getvalue().encode(),
                is_private=1, attached_to_doctype=company.doctype, attached_to_name=company.name)).insert(ignore_permissions=True)

        original = [line("A", "APP"), line("C", "INTERNAL"), line("D", "BANK")]
        first = export(original, "-first")
        options = dict(source_file=first.file_url, currency="USD", portfolio_snapshot=portfolio.name)
        with creation_guard():
            initial = bulk._plan(options)
            assert not initial["issues"], initial["issues"]
            created = bulk._create_imports(initial, options)
        documents = [frappe.get_doc(row.get("doctype") or bulk.DOCTYPE, row["name"]) for row in created]
        snapshots = {(doc.doctype, doc.name): doc.as_dict() for doc in documents}
        assert manual.name in [row["name"] for row in created], "Must link the confirmed manual receipt"
        manual.reload()
        assert len(manual.accounting_sources) == 1

        added = [line("A", "APP"), line("C", "NEW-INTERNAL"), line("D", "NEW-BANK")]
        second = export(list(reversed(original)) + added, "-second")
        updated = dict(options, source_file=second.file_url, previous_source_file=first.file_url, cumulative_import=1)
        with creation_guard():
            plan = bulk._plan(updated)
            assert not plan["issues"], plan["issues"]
            assert len(plan["already_imported"]) == 3 and not plan["missing_origins"]
            assert sum(group["count"] for group in plan["groups"]) == 1
            assert len(plan["complementary"]) == len(plan["deposits"]) == 1
            guard_block(plan)
            bulk._create_imports(plan, updated)
        for doc in documents:
            doc.reload()
            assert doc.as_dict() == snapshots[doc.doctype, doc.name], "An old document changed"
        second_manual.reload()
        assert len(second_manual.accounting_sources) == 1 and second_manual.docstatus == 1
        retry = bulk._plan(updated)
        assert not retry["groups"] and not retry["complementary"] and not retry["deposits"]

        third = export(added, "-third")
        missing = bulk._plan(dict(updated, source_file=third.file_url, previous_source_file=second.file_url))
        assert len(missing["missing_origins"]) == 3, missing["missing_origins"]
        assert not missing["groups"] and not missing["complementary"] and not missing["deposits"]
        for doc in documents:
            doc.reload()
            assert doc.as_dict() == snapshots[doc.doctype, doc.name]
        return {"three_origin_types": True, "old_documents_unchanged": True, "additional_identical_application": True,
                "reordered_retry_without_duplicates": True, "manual_receipts_linked": True,
                "three_absences_reported_without_deletion": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
        assert counts == {table: frappe.db.count(table) for table in tables}, "El ensayo dejó registros"
