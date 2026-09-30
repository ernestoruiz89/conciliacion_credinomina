import frappe


def execute():
    has_evidence = frappe.db.has_column("CN Complementary Item", "fx_evidence")
    # Frappe retains the column after removing the field from the DocType.
    evidence_column = ", fx_evidence" if has_evidence else ""
    rows = frappe.db.sql(
        "select name, voucher, description" + evidence_column + " from `tabCN Complementary Item`",
        as_dict=True,
    )
    for item in rows:
        changes = {"accounting_status": "Registrada" if (item.voucher or "").strip() else "Pendiente de registro"}
        evidence = (item.get("fx_evidence") or "").strip()
        if evidence:
            note = "Fuente de tasa registrada anteriormente: " + evidence
            if note not in (item.description or ""):
                changes["description"] = "\n".join(filter(None, [item.description, note]))
        frappe.db.set_value("CN Complementary Item", item.name, changes, update_modified=False)
