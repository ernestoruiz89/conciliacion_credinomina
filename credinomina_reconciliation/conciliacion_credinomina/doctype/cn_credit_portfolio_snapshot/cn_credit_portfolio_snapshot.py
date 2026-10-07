from calendar import monthrange
from html import escape

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import getdate, now_datetime

from credinomina_reconciliation.credit_portfolio import analyze_portfolio_rows, has_portfolio_employer
from credinomina_reconciliation.parsers import (
    SourceFileError,
    file_sha256,
    has_legacy_numeric_credit_numbers,
    parse_credit_portfolio,
)
from credinomina_reconciliation.portfolio_naming import rename_snapshot_for_date


class CNCreditPortfolioSnapshot(Document):
    def autoname(self):
        # The report date lives inside the attached workbook, so an unsaved
        # draft needs a temporary name until its first successful import.
        self.name = make_autoname("CARTERA-BORRADOR-.YYYY.-.#####")

    def validate(self):
        if self.report_date:
            cut_date = getdate(self.report_date)
            self.cut_month = cut_date.strftime("%Y-%m")
        if self.status in {"Importado", "Importado con alertas"}:
            if not self.report_date or not self.rows:
                frappe.throw(_("Un corte importado debe tener fecha y detalle."))
            self._validate_unique_month()
        self.recalculate_summary()

    def _validate_unique_month(self):
        cut_date = getdate(self.report_date)
        first_day = cut_date.replace(day=1)
        last_day = cut_date.replace(day=monthrange(cut_date.year, cut_date.month)[1])
        duplicates = frappe.get_all(
            self.doctype,
            filters={
                "name": ["!=", self.name or ""],
                "status": ["in", ["Importado", "Importado con alertas"]],
                "report_date": ["between", [first_day, last_day]],
            },
            fields=["name"],
            limit_page_length=1,
        )
        if duplicates:
            frappe.throw(_(
                "Ya existe un corte importado para {0}: {1}. Abra ese corte para actualizarlo."
            ).format(self.cut_month, duplicates[0].name))

    def recalculate_summary(self):
        rows = list(self.rows or [])
        self.row_count = len(rows)
        self.matched_client_count = sum(bool(row.matched_client) for row in rows)
        self.unmatched_client_count = sum(
            not row.matched_client
            and has_portfolio_employer(row)
            for row in rows
        )
        self.active_count = sum(row.credit_lifecycle == "Activo" for row in rows)
        self.canceled_count = sum(row.credit_lifecycle == "Cancelado" for row in rows)
        self.saneado_count = sum(row.credit_lifecycle == "Saneado" for row in rows)


def _attached_file(document):
    if not document.source_file:
        frappe.throw(_("Adjunte el archivo del corte de cartera."))
    file_doc = frappe.get_doc("File", {"file_url": document.source_file})
    if (
        file_doc.attached_to_doctype != document.doctype
        or file_doc.attached_to_name != document.name
    ):
        frappe.throw(_("El archivo debe estar adjunto a este corte de cartera."))
    content = file_doc.get_content()
    if isinstance(content, str):
        content = content.encode("utf-8")
    return file_doc, content


def _has_legacy_numeric_credit_numbers(snapshot_name):
    """Detect snapshots that need the default ``-1`` loan suffix reapplied."""
    credit_numbers = frappe.get_all(
        "CN Credit Portfolio Row",
        filters={"parent": snapshot_name},
        pluck="credit_number",
        limit_page_length=100000,
    )
    return has_legacy_numeric_credit_numbers(credit_numbers)


@frappe.whitelist(methods=["POST"])
def import_portfolio_snapshot(snapshot_name: str):
    snapshot = frappe.get_doc("CN Credit Portfolio Snapshot", snapshot_name)
    snapshot.check_permission("write")
    file_doc, content = _attached_file(snapshot)
    digest = file_sha256(content)
    if snapshot.file_hash == digest and snapshot.status in {
        "Importado", "Importado con alertas"
    } and snapshot.rows and not any(
        has_portfolio_employer(row) and (
            not row.employer or not row.matched_client
            or row.validation_status != "Cliente y empresa validados"
        ) for row in snapshot.rows
    ) and not _has_legacy_numeric_credit_numbers(snapshot.name):
        return {"snapshot_name": snapshot.name, "unchanged": True, "row_count": len(snapshot.rows)}

    duplicate = frappe.db.get_value(
        "CN Credit Portfolio Snapshot",
        {
            "name": ["!=", snapshot.name],
            "file_hash": digest,
            "status": ["in", ["Importado", "Importado con alertas"]],
        },
        "name",
    )
    if duplicate:
        frappe.throw(_("Este archivo ya está cargado en el corte {0}.").format(duplicate))

    try:
        parsed = parse_credit_portfolio(file_doc.file_name, content)
    except SourceFileError as exc:
        snapshot.status = "Fallido"
        snapshot.notes = str(exc)
        snapshot.save()
        frappe.throw(str(exc), title=_("No se pudo importar el corte"))

    created = {}
    analyze_portfolio_rows(parsed, created=created)
    previous_hash = snapshot.file_hash
    previous_report_date = snapshot.report_date
    previous_count = len(snapshot.rows or [])
    snapshot.report_date = parsed[0]["report_date"]
    snapshot.file_hash = digest
    snapshot.set("rows", [])
    for record in parsed:
        snapshot.append("rows", record)
    snapshot.recalculate_summary()
    has_alerts = any(
        not row.credit_number
        or row.credit_lifecycle == "Por revisar"
        or row.validation_status not in {
            "Cliente y empresa validados", "Cliente existente; no es convenio",
            "No aplica; no es convenio",
        }
        or row.employer_match_status not in {
            "Empresa identificada", "No es convenio",
        }
        for row in snapshot.rows
    )
    snapshot.status = "Importado con alertas" if has_alerts else "Importado"
    snapshot.imported_on = now_datetime()
    snapshot.imported_by = frappe.session.user
    snapshot.notes = _("Se importaron {0} créditos de {1}.").format(
        len(parsed), file_doc.file_name
    ) + " " + _("Empresas creadas: {0}. Clientes creados: {1}.").format(
        created["employers"], created["clients"],
    )
    # Version serializes all added/removed child rows into one SQL value. A
    # monthly cut can exceed MariaDB's packet limit even though every row is
    # valid. Keep normal validation/transactions and audit the import compactly.
    snapshot.save(ignore_version=True)
    snapshot_name = rename_snapshot_for_date(snapshot, parsed[0]["report_date"], previous_report_date)
    snapshot.add_comment("Comment", _portfolio_import_audit(
        snapshot, file_doc.file_name, previous_hash, previous_count, created,
    ))
    return {
        "snapshot_name": snapshot_name,
        "row_count": snapshot.row_count,
        "matched_client_count": snapshot.matched_client_count,
        "unmatched_client_count": snapshot.unmatched_client_count,
        "status": snapshot.status,
        "created_employer_count": created["employers"],
        "created_client_count": created["clients"],
    }


def _portfolio_import_audit(snapshot, file_name, previous_hash, previous_count, created):
    """Bounded audit evidence, independent of the size of the child table."""
    details = (
        (_("Archivo"), file_name),
        (_("Fecha del corte"), snapshot.report_date),
        (_("Créditos importados"), snapshot.row_count),
        (_("Empresas creadas"), created["employers"]),
        (_("Clientes creados"), created["clients"]),
        (_("Estado"), snapshot.status),
        (_("Importado por"), snapshot.imported_by),
        (_("Importado el"), snapshot.imported_on),
        ("SHA-256", snapshot.file_hash),
        (_("Créditos anteriores"), previous_count),
        (_("SHA-256 anterior"), previous_hash or _("Sin importación anterior")),
    )
    return "<p>" + escape(_("Importación de cartera completada.")) + "</p><ul>" + "".join(
        "<li>" + escape(str(label)) + ": " + escape(str(value or "0")) + "</li>"
        for label, value in details
    ) + "</ul>"
