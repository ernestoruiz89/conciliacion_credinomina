"""Exercise legacy collection migration with preserved financial links and replay."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as period_api
from credinomina_reconciliation.patches.v1_0 import use_collection_client_link as migration


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        marker = "LINK-MIG-" + frappe.generate_hash(length=8)
        employer = frappe.get_doc(dict(doctype="CN Employer", employer_name=marker,
                                      employer_code=marker, payroll_frequency="Mensual")).insert()
        period = frappe.get_doc(dict(doctype="CN Reconciliation Period", employer=employer.name,
            payroll_month="2096-09-01", reconciliation_mode="Operativa", collection_cycle="Mensual",
            application_basis="Cobranza")).insert()
        content = ("Nro. Cliente,Nombre y Apellidos del Cliente,Nro. Crédito,Monto de la cuota en US$\n"
                   f"{marker},Cliente {marker},MIG-1,50\n"
                   f"{marker},Cliente {marker},MIG-2,30\n"
                   f"{marker},Cliente {marker},MIG-3,20\n").encode()
        with patch.object(period_api, "_attached_file", return_value=(frappe._dict(file_name="c.csv"), content)), \
             patch.object(period_api, "_reconcile_if_sources", return_value=None):
            period_api.import_collection(period.name)
        period.reload()
        fields = ["name", "row_key", "applied_usd", "remitted_usd", "remittance_detail"]
        for row in period.collection_rows:
            frappe.db.set_value("CN Collection Row", row.name, dict(client_number=marker,
                applied_usd=row.expected_usd, remitted_usd=row.expected_usd,
                remittance_detail='[{"deposito":"KEEP"}]'))
        first, second, third = period.collection_rows
        frappe.db.set_value("CN Collection Row", first.name, "client_number", "CONFLICT")
        frappe.db.set_value("CN Collection Row", second.name, "client", "")
        frappe.db.set_value("CN Collection Row", third.name, dict(client="", client_number=marker + "-NEW",
            client_name="Nuevo " + marker, national_id="", employee_number=""))
        frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
        before = frappe.get_all("CN Collection Row", filters={"parent": period.name}, fields=fields, order_by="idx asc")
        real_get_all = frappe.get_all

        def scoped(doctype, *args, **kwargs):
            if doctype == "CN Collection Row":
                kwargs["filters"] = {**kwargs.get("filters", {}), "parent": period.name}
            return real_get_all(doctype, *args, **kwargs)

        with patch.object(migration.frappe, "get_all", side_effect=scoped):
            try:
                migration.execute()
            except frappe.ValidationError as exc:
                assert "contradice" in str(exc), exc
            else:
                raise AssertionError("Conflicting identity was silently migrated")
            assert not frappe.db.get_value("CN Collection Row", second.name, "client")
            assert not frappe.db.exists("CN Client", marker + "-NEW")
            frappe.db.set_value("CN Collection Row", first.name, "client_number", marker)
            migration.execute()
            comments = frappe.db.count("Comment", {"reference_doctype": period.doctype, "reference_name": period.name})
            migration.execute()
            assert frappe.db.count("Comment", {"reference_doctype": period.doctype, "reference_name": period.name}) == comments
        after = frappe.get_all("CN Collection Row", filters={"parent": period.name}, fields=fields, order_by="idx asc")
        assert before == after, (before, after)
        period.reload()
        assert period.status == "Cerrado"
        assert [row.client for row in period.collection_rows] == [marker, marker, marker + "-NEW"]
        assert not any(frappe.db.get_value("CN Collection Row", row.name, "client_number") for row in period.collection_rows)
        assert frappe.db.exists("CN Client", marker + "-NEW")
        return dict(conflict_preflight=True, missing_links_migrated=True, legacy_numbers_retired=True,
                    rows_and_money_preserved=True, closed_period_preserved=True, idempotent=True, rolled_back=True)
    finally:
        frappe.db.rollback()
