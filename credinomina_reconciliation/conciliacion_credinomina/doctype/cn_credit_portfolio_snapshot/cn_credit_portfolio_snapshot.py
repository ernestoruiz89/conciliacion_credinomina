from calendar import monthrange
from contextvars import ContextVar
from html import escape

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import cint, getdate, get_datetime, now_datetime

from credinomina_reconciliation.credit_portfolio import analyze_portfolio_rows, has_portfolio_employer
from credinomina_reconciliation.parsers import (
    SourceFileError,
    file_sha256,
    has_legacy_numeric_credit_numbers,
    parse_credit_portfolio,
)
from credinomina_reconciliation.portfolio_naming import rename_snapshot_for_date

_importing_portfolio = ContextVar("cn_importing_portfolio", default=False)
HEADER_FIELDS = ("source_file", "notes", "disabled")


def _save_import(snapshot, **kwargs):
    """Only the import operation may replace and recalculate existing credits."""
    token = _importing_portfolio.set(True)
    try:
        return snapshot.save(**kwargs)
    finally:
        _importing_portfolio.reset(token)


class CNCreditPortfolioSnapshot(Document):
    def autoname(self):
        # The report date lives inside the attached workbook, so an unsaved
        # draft needs a temporary name until its first successful import.
        self.name = make_autoname("CARTERA-BORRADOR-.YYYY.-.#####")

    def _save(self, ignore_permissions=None, ignore_version=None):
        if self.is_new() or _importing_portfolio.get():
            return super()._save(ignore_permissions=ignore_permissions, ignore_version=ignore_version)
        saved = _update_portfolio_header(self.name, {field: self.get(field) for field in HEADER_FIELDS},
                                         self.modified, docstatus=self.docstatus)
        # Restore authoritative derived header values; ordinary saves cannot
        # change the imported results, hash, counters or child table.
        self.update(saved)
        self.__dict__.pop("__unsaved", None)
        return self

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
        if cint(self.disabled):
            return
        _lock_portfolio_availability()
        cut_date = getdate(self.report_date)
        first_day = cut_date.replace(day=1)
        last_day = cut_date.replace(day=monthrange(cut_date.year, cut_date.month)[1])
        duplicate = frappe.db.get_value(
            self.doctype,
            filters={
                "name": ["!=", self.name or ""],
                "status": ["in", ["Importado", "Importado con alertas"]],
                "disabled": 0,
                "report_date": ["between", [first_day, last_day]],
            },
            fieldname="name",
            for_update=True,
        )
        if duplicate:
            frappe.throw(_(
                "Ya existe un corte activo para {0}: {1}. Desactívelo antes de importar o activar otro corte del mismo mes y año."
            ).format(self.cut_month, duplicate))

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


def _lock_portfolio_availability():
    # One stable parent row serializes imports and activations until commit,
    # including months with no snapshots yet. Never load the portfolio children.
    frappe.db.get_value("DocType", "CN Credit Portfolio Snapshot", "name", for_update=True)


@frappe.whitelist(methods=["POST"])
def set_portfolio_disabled(snapshot_name: str, disabled: int, modified: str):
    """Change availability without loading, validating or rewriting portfolio rows."""
    saved = _update_portfolio_header(snapshot_name, {"disabled": disabled}, modified)
    return {"disabled": saved.disabled, "modified": str(saved.modified), "modified_by": saved.modified_by}


def _update_portfolio_header(snapshot_name, changes, modified, docstatus=None):
    if str(changes.get("disabled")) not in {"0", "1"} or not modified:
        frappe.throw(_("Indique el estado del corte y su fecha de modificación."))
    doctype = "CN Credit Portfolio Snapshot"
    if not int(changes["disabled"]):
        _lock_portfolio_availability()
    saved = frappe.db.get_value(doctype, snapshot_name, "*", as_dict=True, for_update=True)
    if not saved:
        frappe.throw(_("El corte de cartera no existe."), frappe.DoesNotExistError)
    # Construct only the parent to enforce document/user permissions without
    # get_doc(doctype, name), which also loads the entire child table.
    document = frappe.get_doc(dict(saved, doctype=doctype))
    document.check_permission("write")
    document.check_if_locked()
    if document.docstatus == 2:
        frappe.throw(_("No se puede modificar un corte cancelado."))
    if docstatus is not None and int(docstatus) != int(saved.docstatus):
        frappe.throw(_("No se puede cambiar el estado de confirmación del corte al guardar su encabezado."))
    if get_datetime(saved.modified) != get_datetime(modified):
        frappe.throw(_("El corte cambió. Recargue el formulario antes de guardar."),
                     frappe.TimestampMismatchError)
    values = {field: value for field, value in changes.items() if field in HEADER_FIELDS}
    values["disabled"] = int(values["disabled"])
    changed = [[field, saved.get(field), value] for field, value in values.items()
               if (saved.get(field) or "") != (value or "")]
    if "source_file" in values and not values["source_file"]:
        frappe.throw(_("Adjunte el archivo del corte de cartera."), frappe.MandatoryError)
    for field, _previous_value, _value in changed:
        if saved.docstatus == 1 and not document.meta.get_field(field).allow_on_submit:
            frappe.throw(_("El campo {0} no se puede modificar después de confirmar.").format(
                document.meta.get_field(field).label), frappe.UpdateAfterSubmitError)
    if changed:
        document.update(values)
        if not document.disabled and document.report_date and document.status in {"Importado", "Importado con alertas"}:
            document._validate_unique_month()
        document.modified = now_datetime()
        document.modified_by = frappe.session.user
        values.update(modified=document.modified, modified_by=document.modified_by)
        frappe.db.set_value(doctype, snapshot_name, values, update_modified=False)
        frappe.get_doc({"doctype": "Version", "ref_doctype": doctype, "docname": snapshot_name,
            "data": frappe.as_json({"changed": changed})}).insert(ignore_permissions=True)
        frappe.clear_document_cache(doctype, snapshot_name)
        document.notify_update()
        saved.update(values)
    return saved


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
    _lock_portfolio_availability()
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
            "disabled": 0,
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
        _save_import(snapshot)
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
    _save_import(snapshot, ignore_version=True)
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
