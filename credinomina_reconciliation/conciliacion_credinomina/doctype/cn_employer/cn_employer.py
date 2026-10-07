import frappe
from frappe import _
from frappe.model.document import Document
from credinomina_reconciliation.employer_naming import employer_label_key
from credinomina_reconciliation.rounding import money


class CNEmployer(Document):
    def validate(self):
        self.employer_name = (self.employer_name or "").strip()
        self.short_name = (self.get("short_name") or "").strip()
        if not self.employer_name:
            frappe.throw(_("Indique el nombre de la empresa."))
        previous = self.get_doc_before_save()
        self.reconciliation_revision = 0
        if previous:
            from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
            lock_cash_pool([self.name])
            self.reconciliation_revision = frappe.db.sql(
                "SELECT reconciliation_revision FROM `tabCN Employer` WHERE name=%s FOR UPDATE", self.name,
            )[0][0]
        if (
            previous and previous.name == previous.employer_name
            and self.name != self.employer_name
        ):
            frappe.throw(_("Para cambiar el nombre de la empresa, use Renombrar."))
        tolerance = money(self.rounding_tolerance_usd)
        self.rounding_tolerance_usd = tolerance
        if tolerance < 0 or tolerance > 0.10:
            frappe.throw(_("La tolerancia automática debe estar entre US$ 0.00 y US$ 0.10."))
        seen_aliases = set()
        from credinomina_reconciliation.paying_employers import validate_paying_for
        validate_paying_for(self)
        for alias in self.aliases or []:
            alias.alias_name = (alias.alias_name or "").strip()
            key = employer_label_key(alias.alias_name)
            if not key or key in seen_aliases:
                frappe.throw(_("Los nombres alternativos deben ser distintos y no vacíos."))
            seen_aliases.add(key)

    def before_rename(self, old, new, merge=False):
        if merge:
            frappe.throw(_("No se pueden fusionar empresas Credinómina."))
        return (new or "").strip()

    def after_rename(self, old, new, merge=False):
        frappe.db.set_value(self.doctype, new, "employer_name", new, update_modified=False)
        self.employer_name = new

    def on_update(self):
        previous = self.get_doc_before_save()
        # Alias edits only update identification for the next analysis. Do not
        # scan accounting history or redistribute deposits while saving names.
        if not previous or (
            money(previous.rounding_tolerance_usd) == money(self.rounding_tolerance_usd)
            and previous.employer_code == self.employer_code
        ):
            return
        if not frappe.db.exists(
            "CN Accounting Import",
            {"employer": self.name, "status": ["in", ["Importado", "Importado con excepciones"]]},
        ):
            return
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
            _reconcile_sources,
        )

        _reconcile_sources(self.name)


@frappe.whitelist(methods=["POST"])
def add_employer_alias(employer: str, alias_name: str):
    from credinomina_reconciliation.employer_naming import UNIDENTIFIED_EMPLOYER, attach_employer_aliases

    document = frappe.get_doc("CN Employer", employer)
    document.check_permission("write")
    alias_name = " ".join((alias_name or "").split())
    key = employer_label_key(alias_name)
    if not key:
        frappe.throw(_("Indique el alias que aparece en el archivo."))
    if len(alias_name) > 140:
        frappe.throw(_("El alias no puede superar 140 caracteres."))
    if document.name == UNIDENTIFIED_EMPLOYER or document.employer_name == UNIDENTIFIED_EMPLOYER:
        frappe.throw(_("Seleccione una empresa identificada para registrar el alias."))
    # Check all names/codes too: a higher-priority name must not silently win
    # over the company the user selected. Do not disclose inaccessible names.
    companies = frappe.get_all("CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0)
    attach_employer_aliases(companies)
    for company in companies:
        labels = [company.get(field) for field in ("name", "employer_name", "employer_code")]
        labels.extend(company.get("aliases", []))
        if company.name != document.name and any(employer_label_key(label) == key for label in labels):
            frappe.throw(_("Este nombre o alias ya identifica a otra empresa. Revise los nombres alternativos antes de continuar."))
    if any(employer_label_key(row.alias_name) == key for row in document.aliases or []):
        return {"employer": document.name, "alias_name": alias_name, "added": False}
    document.append("aliases", {"alias_name": alias_name})
    document.save()
    return {"employer": document.name, "alias_name": alias_name, "added": True}
