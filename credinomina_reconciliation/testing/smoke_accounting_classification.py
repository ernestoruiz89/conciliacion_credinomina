"""Mixed import regression against a real Frappe site; rollback-only."""

import csv
import io
from unittest.mock import patch

import frappe
from frappe.utils.file_manager import save_file

from credinomina_reconciliation import bulk_accounting_import as bulk
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "class-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                                   "employer_code": marker, "payroll_frequency": "Mensual"}).insert().name
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF",
                         "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "EMPRESA"])
        data = [
            ["1602", "2025-04-15", 12, 5, marker + "-1", "REF", "PAGO", 366.24, 0, employer],
            ["1602", "2025-04-15", 12, 16, marker + "-2", "REF ND", "NOTA DE DEBITO", 0, 73.25, employer],
            ["1602", "2025-04-15", 1, 1, marker + "-3", "TEXTO NO_REF", "INTERNO", 36.62, 0, ""],
            ["1602", "2025-04-15", 99, 99, marker + "-4", "", "DESCONOCIDO", 36.62, 0, ""],
            ["1602", "2025-04-15", 12, 6, marker + "-5", "REF", "REVERSIÓN", 0, 36.62, employer],
        ]
        writer.writerows(data)
        source = save_file(marker + ".csv", stream.getvalue().encode(), None, None, is_private=1)
        options = {"source_file": source.file_url, "currency": "NIO", "manual_fx_rate": "36.6243"}
        plan = bulk._plan(options)
        assert not plan["issues"], plan["issues"]
        assert len(plan["groups"]) == 1 and len(plan["complementary"]) == 4
        created = bulk._create_imports(plan, options)
        assert len(created) == 5
        applications = frappe.get_doc("CN Accounting Import", created[0]["name"])
        assert len(applications.rows) == 1 and applications.total_usd == 10
        items = [frappe.get_doc("CN Complementary Item", entry["name"]) for entry in created[1:]]
        assert all(item.docstatus == 0 and not item.reference and item.source_file for item in items)
        assert all(item.source_currency == "NIO" and item.source_fx_rate == 36.6243 for item in items)
        assert len([item for item in items if not item.employer]) == 2
        for item in items:
            try:
                item.submit()
            except frappe.ValidationError:
                item.reload()
            else:
                raise AssertionError("Unreviewed accounting item must not submit")
        nd = items[0]
        nd.related_application = applications.rows[0].name
        nd.review_action = "Reversión identificada"
        nd.review_notes = "Vínculo revisado; no modifica el importe aplicado"
        nd.save()
        assert nd.related_import == applications.name
        assert frappe.db.get_value("CN Source Row", applications.rows[0].name, "amount_usd") == 10
        internal = items[1]
        internal.employer = employer
        internal.review_action = "Partida de depósito"
        internal.reference = marker + "-DEP"
        internal.category = "Cobranza administrativa"
        internal.review_notes = "Cobranza identificada; importe y signo revisados"
        internal.amount_reviewed = 1
        internal.flags.defer_reconciliation = True
        internal.submit()
        assert internal.docstatus == 1 and internal.review_status == "Lista para conciliar"
        repeat = bulk._plan(options)
        assert not repeat["groups"] and not repeat["complementary"] and len(repeat["already_imported"]) == 5
        # Individual import routes non-payments the same way and does not duplicate drafts.
        individual_stream = io.StringIO()
        writer = csv.writer(individual_stream)
        writer.writerow(stream.getvalue().splitlines()[0].split(","))
        writer.writerow(["1602", "2025-05-01", 1, 1, marker + "-6", "texto", "INTERNO INDIVIDUAL", 36.62, 0, employer])
        individual_file = save_file(marker + "-individual.csv", individual_stream.getvalue().encode(), None, None, is_private=1)
        individual = frappe.get_doc({"doctype": "CN Accounting Import", "employer": employer,
                                     "currency": "NIO", "manual_fx_rate": 36.6243, "source_file": individual_file.file_url}).insert()
        # Attach evidence to the parent as the form upload normally does.
        individual_file.reload()
        individual_file.attached_to_doctype = individual.doctype
        individual_file.attached_to_name = individual.name
        individual_file.attached_to_field = "source_file"
        individual_file.save()
        with patch.object(accounting, "reconcile_all_sources", return_value={}):
            first = accounting.import_source_file(individual.name)
            reloaded = frappe.get_doc("CN Accounting Import", first["import_name"])
            assert reloaded.total_usd == 0 and reloaded.rows[0].complementary_item
            link = reloaded.rows[0].complementary_item
            second = accounting.import_source_file(reloaded.name)
            assert frappe.get_doc("CN Accounting Import", second["import_name"]).rows[0].complementary_item == link
        return {"applications": 1, "review_drafts": 4, "without_company": 2,
                "review_submit_guard": "OK", "reviewed_complement_confirmation": "OK", "original_link_no_financial_effect": "OK",
                "bulk_duplicates": "OK", "individual_reimport": "OK", "rolled_back": True}
    finally:
        frappe.db.rollback()
