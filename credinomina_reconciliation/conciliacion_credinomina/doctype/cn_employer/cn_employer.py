import frappe
from frappe import _
from frappe.model.document import Document
from credinomina_reconciliation.employer_naming import employer_label_key
from credinomina_reconciliation.rounding import money


class CNEmployer(Document):
    def validate(self):
        self.employer_name = (self.employer_name or "").strip()
        if not self.employer_name:
            frappe.throw(_("Indique el nombre de la empresa."))
        previous = self.get_doc_before_save()
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
        if previous:
            previous_aliases = sorted(
                employer_label_key(row.alias_name) for row in previous.aliases or []
            )
            current_aliases = sorted(
                employer_label_key(row.alias_name) for row in self.aliases or []
            )
            if (
                money(previous.rounding_tolerance_usd) == money(self.rounding_tolerance_usd)
                and previous.employer_code == self.employer_code
                and previous_aliases == current_aliases
            ):
                return
        if not previous and not (self.aliases or []):
            return
        if not frappe.db.exists(
            "CN Accounting Import",
            {"status": ["in", ["Importado", "Importado con excepciones"]]},
        ):
            return
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import (
            reconcile_all_sources,
        )

        reconcile_all_sources()
