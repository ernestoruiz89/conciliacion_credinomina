"""Preview and resumable, bounded background creation of company/day imports."""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from credinomina_reconciliation.accounting_batch import accounting_group_csv, accounting_source_records, group_applications, movement_key
from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.accounting_assignments import apply_assignments
from credinomina_reconciliation.accounting_review import create_review_items, plan_review_items
from credinomina_reconciliation.accounting_deposits import plan_deposits, create_deposits
from credinomina_reconciliation.client_registry import enrich_source_import_clients
from credinomina_reconciliation.credit_portfolio import enrich_accounting_records
from credinomina_reconciliation.employer_naming import attach_employer_aliases, UNIDENTIFIED_EMPLOYER, ensure_unidentified_employer
from credinomina_reconciliation.rounding import money_float, sum_money
from credinomina_reconciliation.file_references import attach_existing_file, readable_file
from credinomina_reconciliation.parsers import (
    SourceFileError, apply_accounting_currency_override, clean_text, file_sha256,
    parse_accounting_movements, source_key,
)

DOCTYPE = "CN Accounting Import"
MAX_MOVEMENTS = 100_000


def _key(token):
    return f"cn-accounting-batch:{token}"


def _store(token, state):
    from credinomina_reconciliation.accounting_batch_store import save_state
    save_state(token, state)


def _permissions():
    if not frappe.has_permission(DOCTYPE, "create") or not frappe.has_permission(DOCTYPE, "write"):
        frappe.throw(_("No tiene permisos para crear importaciones contables."), frappe.PermissionError)


def _required_portfolio_snapshot(value):
    name = clean_text(value)
    if not name:
        frappe.throw(_("Seleccione un Corte de cartera importado antes de analizar o procesar la carga masiva. Si la carga fue guardada sin corte, seleccione uno y genere un nuevo análisis."))
    snapshot = frappe.get_doc("CN Credit Portfolio Snapshot", name)
    snapshot.check_permission("read")
    if snapshot.get("disabled"):
        frappe.throw(_("El Corte de cartera está desactivado. Seleccione un corte activo."))
    if snapshot.status not in {"Importado", "Importado con alertas"}:
        frappe.throw(_("El Corte de cartera debe estar importado antes de usarlo en la carga masiva."))
    return name


def _validate_portfolio_companies(snapshot, groups):
    companies = {group["employer"] for group in groups} - {UNIDENTIFIED_EMPLOYER}
    if not companies:
        return
    present = set(frappe.get_all("CN Credit Portfolio Row", filters={
        "parent": snapshot, "parenttype": "CN Credit Portfolio Snapshot",
        "employer": ["in", sorted(companies)]}, pluck="employer", limit_page_length=0))
    missing = companies - present
    if missing:
        frappe.throw(_("El Corte de cartera seleccionado no contiene créditos de estas empresas: {0}. Seleccione un corte compatible y genere un nuevo análisis.").format(
            ", ".join(sorted(missing))))


def _state(token, for_update=False):
    _permissions()
    from credinomina_reconciliation.accounting_batch_store import load_state
    # Read-only recovery of previews made before durable batches were deployed.
    # Their options include employer choices; the next new analysis retains them.
    state = load_state(token, for_update=for_update) or frappe.cache.get_value(_key(token))
    if not state or state["user"] != frappe.session.user:
        frappe.throw(_("La carga no existe, expiró o pertenece a otro usuario."), frappe.PermissionError)
    return state


def _file(url):
    # An upload may be unattached or already attached to a readable document.
    for name in frappe.get_all("File", filters={"file_url": url}, pluck="name"):
        document = frappe.get_doc("File", name)
        if document.has_permission("read"):
            content = document.get_content()
            if isinstance(content, str):
                content = content.encode("utf-8")
            if len(content) > 20 * 1024 * 1024:
                frappe.throw(_("El archivo supera 20 MB. Divida la carga en varios archivos."))
            return document, content
    frappe.throw(_("No tiene acceso al archivo seleccionado."), frappe.PermissionError)


def _plan(options):
    _permissions()
    snapshot = _required_portfolio_snapshot(options.get("portfolio_snapshot"))
    file_doc, content = _file(options["source_file"])
    employers = frappe.get_list(
        "CN Employer", fields=["name", "employer_name", "employer_code"], limit_page_length=0,
    )
    attach_employer_aliases(employers)
    fallback = options.get("employer") or ""
    if fallback:
        frappe.get_doc("CN Employer", fallback).check_permission("read")
    if not frappe.has_permission("CN Credit Portfolio Snapshot", "read"):
        frappe.throw(_("Se requiere acceso de lectura a los cortes de cartera."), frappe.PermissionError)
    records = apply_accounting_currency_override(
        parse_accounting_movements(file_doc.file_name, content),
        options["currency"], options.get("manual_fx_rate", 0),
    )
    if len(records) > MAX_MOVEMENTS:
        frappe.throw(_("La carga supera {0} movimientos. Divida el archivo.").format(f"{MAX_MOVEMENTS:,}"))
    file_hash = file_sha256(content)
    assignments = options.get("employer_assignments") or {}
    if assignments and options.get("assignments_file_hash") != file_hash:
        raise SourceFileError("El archivo cambió. Quite las selecciones anteriores y analice el nuevo archivo.")
    repeated_evidence, seen_evidence = [], set()
    for row in records:
        key = row["accounting_source_key"]
        if key in seen_evidence and row.get("event_type") != "Aplicacion":
            repeated_evidence.append({"row": row["source_row"], "reason": "Movimiento similar a otra línea del archivo; se conservará como registro independiente en borrador"})
        seen_evidence.add(key)
    identify_lines(records, file_hash)
    # Preview must never create clients or modify portfolio/master documents.
    records = enrich_accounting_records(records, snapshot, register_clients=False)
    parents = frappe.get_all(
        DOCTYPE, filters={"status": ["!=", "Fallido"]}, fields=["name", "employer", "file_hash", "bulk_source_hash"],
        limit_page_length=0,
    )
    companies = {parent.name: parent.employer for parent in parents}
    same_file_parents = {parent.name for parent in parents if file_hash in (parent.file_hash, parent.bulk_source_hash)}
    dates = [str(row["event_date"])[:10] for row in records if row.get("event_date")]
    existing = set()
    imported_lines = set()
    if dates and companies:
        for row in frappe.get_all(
            "CN Source Row", filters={"parenttype": DOCTYPE, "event_type": "Aplicacion",
                                      "event_date": ["between", [min(dates), max(dates)]]},
            fields=["parent", "source_row", "event_type", "loan_number", "reference", "currency", "amount", "event_date", "voucher"],
            limit_page_length=0,
        ):
            if row.parent in companies:
                existing.add((companies[row.parent], movement_key(row)))
            if row.parent in same_file_parents:
                imported_lines.add(row.source_row)
    review_records = [dict(row, event_type="Aplicacion") for row in records if row.get("event_type") == "Ajuste"]
    enrich_accounting_records(review_records, snapshot, register_clients=False)
    for row in review_records:
        row["event_type"] = "Ajuste"
    # Review records carry their own enriched portfolio evidence; apply choices
    # only after that enrichment and never mutate the original employer text.
    candidates = [row for row in records if row.get("event_type") != "Ajuste"] + review_records
    apply_assignments(candidates, employers, assignments)
    review_records = plan_review_items(review_records, employers, fallback)
    deposit_records = plan_deposits(records, employers, fallback)
    applications = [row for row in records if row.get("event_type") != "Ajuste" and row.get("accounting_classification") != "Depósito"]
    plan = group_applications([row for row in applications if row["source_row"] not in imported_lines], employers, fallback, existing)
    _validate_portfolio_companies(snapshot, plan["groups"])
    plan["already_imported"] = [{"row": row["source_row"], "reason": "Esta misma fila del mismo archivo ya fue importada; use su documento para reprocesarla"}
                                for row in applications if row["source_row"] in imported_lines]
    plan["duplicates"].extend(repeated_evidence)
    if deposit_records and not frappe.has_permission("CN Remittance Allocation", "create"):
        frappe.throw(_("Se requiere permiso para crear depósitos."), frappe.PermissionError)
    # Keep already imported evidence in the plan: creation is idempotent and
    # checks currency/rate consistency instead of silently discarding changes.
    plan["deposits"] = deposit_records
    if review_records and not frappe.has_permission("CN Complementary Item", "create"):
        frappe.throw(_("Se requiere permiso para crear partidas complementarias en revisión."), frappe.PermissionError)
    existing_review = set(frappe.get_all("CN Complementary Item", filters={
        "accounting_source_key": ["in", [row["accounting_source_key"] for row in review_records]],
    }, pluck="accounting_source_key")) if review_records else set()
    plan["complementary"] = []
    for row in review_records:
        if not row.get("event_date"):
            plan["issues"].append({"row": row["source_row"], "reason": "Falta fecha válida en el movimiento por revisar"})
        elif row["accounting_source_key"] in existing_review:
            plan["already_imported"].append({"row": row["source_row"], "reason": "Esta misma fila del mismo archivo ya tiene una partida complementaria"})
        else:
            existing_review.add(row["accounting_source_key"])
            plan["complementary"].append(row)
    plan.update(file_hash=file_sha256(content), file_name=file_doc.file_name)
    return plan


def _summary(plan):
    groups = [{key: value for key, value in group.items() if key != "rows"} for group in plan["groups"]]
    return {
        "file_hash": plan.get("file_hash"),
        "sections": {key: _identification_summary(plan, unidentified)
                     for key, unidentified in (("identified", False), ("unidentified", True))},
        "groups": groups[:100], "group_count": len(groups), "rows": sum(group["count"] for group in groups),
        "complementary_count": len(plan.get("complementary", [])),
        "deposit_count": len(plan.get("deposits", [])),
        "unidentified_count": sum(group["count"] for group in groups if group["employer"] == UNIDENTIFIED_EMPLOYER)
            + sum(row.get("resolved_employer") == UNIDENTIFIED_EMPLOYER
                  for row in plan.get("complementary", []) + plan.get("deposits", [])),
        "deposits": [{"row": row["source_row"], "employer": row.get("resolved_employer"),
                      "currency": row["deposit_currency"], "amount": row["deposit_amount"],
                      "reference": row["bank_deposit_reference"]} for row in plan.get("deposits", [])[:100]],
        "complementary": [{"row": row["source_row"], "classification": row["accounting_classification"],
                           "employer": row.get("resolved_employer"), "reason": row["classification_reason"]}
                          for row in plan.get("complementary", [])[:100]],
        **{key: plan[key][:100] for key in ("issues", "duplicates", "excluded", "already_imported")},
        **{f"{key}_count": len(plan[key]) for key in ("issues", "duplicates", "excluded", "already_imported")},
    }


def _identification_summary(plan, unidentified):
    def belongs(employer):
        return (not employer or employer == UNIDENTIFIED_EMPLOYER) == unidentified

    groups = [group for group in plan["groups"] if belongs(group["employer"])]
    items = [row for row in plan.get("complementary", []) if belongs(row.get("resolved_employer"))]
    deposits = [row for row in plan.get("deposits", []) if belongs(row.get("resolved_employer"))]
    applications = [row for group in groups for row in group["rows"]]
    return {
        "groups": [{key: value for key, value in group.items() if key != "rows"} for group in groups[:100]],
        "group_count": len(groups),
        "rows": len(applications), "application_total_usd": money_float(sum_money(row.get("amount_usd") for row in applications)),
        "applications": [{"row": row["source_row"], "event_date": row.get("event_date"),
                          "client_name": row.get("client_name"), "loan_number": row.get("loan_number"),
                          "employer_text": row.get("employer_text"), "voucher": row.get("voucher"),
                          "description": row.get("source_description") or row.get("description") or "",
                          "total_usd": row.get("amount_usd"), "reason": row.get("portfolio_validation_status")}
                         for row in applications[:100]] if unidentified else [],
        "complementary_count": len(items),
        "complementary": [{"row": row["source_row"], "classification": row["accounting_classification"],
                           "employer": row.get("resolved_employer"), "reason": row["classification_reason"],
                           "employer_text": row.get("employer_text"),
                           "description": row.get("source_description") or row.get("description") or ""} for row in items[:100]],
        "deposit_count": len(deposits),
        "deposits": [{"row": row["source_row"], "employer": row.get("resolved_employer"),
                      "currency": row["deposit_currency"], "amount": row["deposit_amount"],
                      "reference": row["bank_deposit_reference"], "employer_text": row.get("employer_text"),
                      "description": row.get("source_description") or row.get("description") or ""}
                     for row in deposits[:100]],
    }


def _signature(plan):
    # Confirmation cannot silently import a different grouping or amount.
    return source_key(plan["file_hash"], json.dumps([plan["groups"], plan.get("complementary", []), plan.get("deposits", [])], sort_keys=True, default=str))


@frappe.whitelist(methods=["POST"])
def preview_bulk_import(source_file, currency, manual_fx_rate=0, employer="", portfolio_snapshot="", historical_backfill=0,
                        employer_assignments=None, assignments_file_hash=""):
    _permissions()
    portfolio_snapshot = _required_portfolio_snapshot(portfolio_snapshot)
    _file(source_file)
    from credinomina_reconciliation.accounting_batch_store import ensure_mutex
    ensure_mutex()
    assignments = frappe.parse_json(employer_assignments) if isinstance(employer_assignments, str) else employer_assignments or {}
    if not isinstance(assignments, dict) or len(assignments) > MAX_MOVEMENTS:
        frappe.throw(_("Las asignaciones de empresa no tienen un formato válido."))
    token = frappe.generate_hash(length=32)
    state = {"user": frappe.session.user, "status": "En cola", "phase": "preview", "options": {
        "source_file": source_file, "currency": clean_text(currency), "manual_fx_rate": manual_fx_rate,
        "employer": clean_text(employer), "portfolio_snapshot": clean_text(portfolio_snapshot),
        "historical_backfill": cint(historical_backfill),
        "employer_assignments": assignments, "assignments_file_hash": clean_text(assignments_file_hash),
    }}
    _store(token, state)
    _enqueue(token, state)
    return {"token": token}


def _enqueue(token, state):
    try:
        state["job_id"] = f"cn-accounting-batch-{token}-{frappe.generate_hash(length=12)}"
        _store(token, state)
        frappe.enqueue(
            "credinomina_reconciliation.bulk_accounting_import.run_bulk_job",
            queue="long", timeout=3600, token=token, user=state["user"],
            attempt=state["job_id"],
            enqueue_after_commit=True, job_id=state["job_id"],
        )
    except Exception:
        state.update(status="Error", error=_("No se pudo encolar la carga. Revise los workers y vuelva a intentarlo."))
        _store(token, state)
        raise


@frappe.whitelist(methods=["POST"])
def confirm_bulk_import(token):
    from credinomina_reconciliation.accounting_batch_store import ensure_mutex
    _state(token)
    ensure_mutex()
    state = _state(token, for_update=True)
    _required_portfolio_snapshot(state["options"].get("portfolio_snapshot"))
    if state["status"] != "Vista previa" or state["summary"]["issues_count"]:
        frappe.throw(_("Primero genere una vista previa sin errores."))
    if state.get("draft_assignments", state["options"].get("employer_assignments", {})) != state["options"].get("employer_assignments", {}):
        frappe.throw(_("Hay empresas seleccionadas sin analizar. Genere un nuevo análisis antes de importar."))
    if not state["summary"]["rows"] and not state["summary"].get("complementary_count") and not state["summary"].get("deposit_count"):
        frappe.throw(_("No hay movimientos nuevos para importar."))
    state.update(status="En cola", phase="create")
    _enqueue(token, state)
    return {"token": token}


@frappe.whitelist()
def get_bulk_import_status(token):
    state = _state(token)
    interrupted = False
    job_status = None
    if state["status"] in {"En cola", "Procesando"} and state.get("job_id"):
        from frappe.utils.background_jobs import get_job

        job = get_job(state["job_id"])
        job_status = job.get_status() if job else None
        interrupted = not job or job_status in {"failed", "stopped", "canceled", "finished"}
    output = {key: state.get(key) for key in ("status", "phase", "summary", "created", "error", "progress", "options",
                                             "completed_blocks", "total_blocks", "created_count", "draft_assignments")}
    output["resumable"] = interrupted or state["status"] == "Error"
    if job_status == "started":
        output["status"] = "Procesando"
    if interrupted:
        output.update(status="Error", error=_("El worker no está activo. Puede reanudar: los bloques guardados y las empresas seleccionadas se conservan."))
    if state.get("total_blocks"):
        from credinomina_reconciliation.accounting_batch_store import results
        output["created"] = results(token)
    return output


@frappe.whitelist()
def latest_bulk_import():
    _permissions()
    from credinomina_reconciliation.accounting_batch_store import BATCH, MUTEX
    names = frappe.get_all(BATCH, filters={"owner": frappe.session.user, "name": ["!=", MUTEX]},
                           order_by="creation desc", pluck="name", limit_page_length=1)
    return {"token": names[0] if names else None}


@frappe.whitelist(methods=["POST"])
def save_bulk_employer_choices(token, employer_assignments, file_hash):
    """Save pre-analysis edits as a draft, never change a confirmed block plan."""
    _state(token)
    state = _state(token, for_update=True)
    if state["phase"] != "preview" or state["status"] not in {"Vista previa", "Error"}:
        frappe.throw(_("La carga ya está en proceso. No se pueden cambiar sus empresas seleccionadas."))
    if file_hash != state.get("summary", {}).get("file_hash"):
        frappe.throw(_("Las selecciones no corresponden al archivo analizado."))
    assignments = frappe.parse_json(employer_assignments)
    if not isinstance(assignments, dict) or len(assignments) > MAX_MOVEMENTS:
        frappe.throw(_("Las asignaciones de empresa no tienen un formato válido."))
    for company in set(assignments.values()):
        frappe.get_doc("CN Employer", company).check_permission("read")
    state["draft_assignments"] = assignments
    _store(token, state)
    return {"saved": True}


@frappe.whitelist(methods=["POST"])
def resume_bulk_import(token):
    from credinomina_reconciliation.accounting_batch_store import ensure_mutex
    _state(token)  # Permission/ownership check before locking anything.
    ensure_mutex()
    state = _state(token, for_update=True)
    _required_portfolio_snapshot(state["options"].get("portfolio_snapshot"))
    if state["status"] not in {"Error", "En cola", "Procesando"}:
        frappe.throw(_("Esta carga no necesita reanudarse."))
    if state.get("job_id"):
        from frappe.utils.background_jobs import get_job
        job = get_job(state["job_id"])
        # A Redis/RQ inspection error propagates: never infer that a worker died.
        if job and job.get_status() not in {"failed", "stopped", "canceled", "finished"}:
            frappe.throw(_("El trabajo sigue activo o en cola. Espere antes de reanudar."))
    state.update(status="En cola", error="")
    _enqueue(token, state)
    return {"token": token}


def _create_imports(plan, options, progress=None):
    """One transaction, no reconciliation or period changes. Caller owns commit."""
    snapshot = _required_portfolio_snapshot(options.get("portfolio_snapshot"))
    _validate_portfolio_companies(snapshot, plan["groups"])
    created = []
    source = readable_file(options["source_file"])
    source_records = None
    if any("csv_content" not in group for group in plan["groups"]):
        source, content = _file(options["source_file"])
        source_records = accounting_source_records(source.file_name, content)
    if any(group["employer"] == UNIDENTIFIED_EMPLOYER for group in plan["groups"]):
        ensure_unidentified_employer()
    records = []
    for group in plan["groups"]:
        for record in group["rows"]:
            if group["employer"] != UNIDENTIFIED_EMPLOYER and not record.get("_manual_employer") and not record.get("employer_text") and not record.get("portfolio_employer"):
                record["employer_text"] = group["employer"]
            records.append(record)
    # The preview already selected the dated cut. Reuse that evidence and build
    # the registry once for the whole batch, without changing its company scope.
    enrich_source_import_clients(records)
    for index, group in enumerate(plan["groups"], 1):
        employer = group["employer"]
        frappe.get_doc("CN Employer", employer).check_permission("read")
        records = group["rows"]
        csv_content = (group["csv_content"].encode("utf-8") if "csv_content" in group
                       else accounting_group_csv(source_records, records))
        document = frappe.get_doc({
            "doctype": DOCTYPE, "employer": employer, "source_file": options["source_file"],
            "currency": options["currency"], "manual_fx_rate": options.get("manual_fx_rate", 0),
            "portfolio_snapshot": snapshot,
            "historical_backfill": options.get("historical_backfill", 0),
            "bulk_source_hash": plan["file_hash"], "bulk_event_date": group["event_date"],
            "bulk_source_file": options["source_file"],
            "file_hash": file_sha256(csv_content),
            "status": "Importado", "imported_on": now_datetime(), "imported_by": frappe.session.user,
            "notes": _("Carga masiva de {0}. Fecha: {1}. Pendiente de conciliar esta empresa.").format(
                plan["file_name"], group["event_date"])
                + (_(" Empresa pendiente de identificar; se conservó el texto original del movimiento.") if employer == UNIDENTIFIED_EMPLOYER else "")
                + (_(" Empresa seleccionada manualmente antes de importar; decisión conservada en CN_EMPRESA_ASIGNADA del CSV.") if any(row.get("_manual_employer") for row in records) else ""),
            "rows": [{**record, "effective": 1, "match_status": "Pendiente", "deposit_match_status": "Pendiente"}
                     for record in records],
        }).insert()
        # Let File own the single write and rollback cleanup. The legacy
        # save_file utility prewrites the blob before File.insert writes again,
        # leaving an unreferenced CSV (especially when its UTF-8 BOM is decoded).
        csv_file = frappe.get_doc({
            "doctype": "File", "file_name": f"{document.name}.csv", "content": csv_content,
            "attached_to_doctype": DOCTYPE, "attached_to_name": document.name,
            "attached_to_field": "source_file", "is_private": 1,
        }).insert(ignore_permissions=True)
        document.source_file = csv_file.file_url
        stored_content = frappe.get_doc("File", csv_file.name).get_content()
        if isinstance(stored_content, str):
            stored_content = stored_content.encode("utf-8")
        # File.get_content may strip the UTF-8 BOM; use the exact same input
        # representation as the individual importer for duplicate detection.
        document.file_hash = file_sha256(stored_content)
        document.save()
        # Keep the original report separately as provenance, never as reimport input.
        attach_existing_file(source, DOCTYPE, document.name, "bulk_source_file")
        created.append({"name": document.name, "employer": employer, "event_date": group["event_date"],
                        "rows": len(records), "total_usd": document.total_usd,
                        "description": records[0].get("source_description") or records[0].get("description") or "" if employer == UNIDENTIFIED_EMPLOYER else ""})
        if progress:
            progress(index, len(plan["groups"]))
    for item in create_review_items(plan.get("complementary", []), options["source_file"], plan["file_hash"]):
        created.append({"doctype": "CN Complementary Item", "name": item.name, "employer": item.employer,
                        "event_date": item.posting_date, "rows": 1, "total_usd": item.amount_usd,
                        "description": item.source_description if item.employer == UNIDENTIFIED_EMPLOYER else ""})
    create_deposits(plan.get("deposits", []), options["source_file"], plan["file_hash"])
    for row in plan.get("deposits", []):
        created.append({"doctype": "CN Remittance Allocation", "name": row["remittance_allocation"],
                        "employer": row.get("resolved_employer"), "event_date": row["event_date"],
                        "rows": 1, "total_usd": row["deposit_usd"],
                        "description": row.get("source_description") or "" if row.get("resolved_employer") == UNIDENTIFIED_EMPLOYER else ""})
    return created


def _exclude_completed_applications(plan):
    """Another batch may have committed overlapping physical rows since preview.

    Caller holds the DB creation lock. Business-identical lines at different
    source positions are deliberately NOT excluded.
    """
    rows = [row["source_row"] for group in plan["groups"] for row in group["rows"]]
    if not rows:
        return
    existing = frappe.db.sql("""
        select r.source_row from `tabCN Source Row` r
        inner join `tabCN Accounting Import` p on p.name=r.parent
        where r.parenttype='CN Accounting Import' and p.status!='Fallido'
        and (p.bulk_source_hash=%s or p.file_hash=%s)
        and r.source_row in %s for update
    """, (plan["file_hash"], plan["file_hash"], tuple(rows)))
    imported = {row[0] for row in existing}
    kept = []
    for group in plan["groups"]:
        remaining = [row for row in group["rows"] if row["source_row"] not in imported]
        if not remaining:
            continue
        if len(remaining) != len(group["rows"]):
            # Use the frozen raw cells, never re-read the large original file.
            raw = accounting_source_records("group.csv", group["csv_content"].encode("utf-8"))
            group["csv_content"] = accounting_group_csv(raw, remaining).decode("utf-8")
            group.update(rows=remaining, count=len(remaining), total_usd=money_float(sum_money(row["amount_usd"] for row in remaining)))
        kept.append(group)
    plan["groups"] = kept


def run_bulk_job(token, user, attempt=None):
    from credinomina_reconciliation import accounting_batch_store as checkpoint

    frappe.set_user(user)
    state = _state(token)
    try:
        checkpoint.ensure_mutex()
        state = _state(token, for_update=True)
        if (attempt and state.get("job_id") != attempt) or state["status"] not in {"En cola", "Procesando"}:
            return  # Obsolete/repeated delivery; never create the block twice.
        if state["phase"] == "preview":
            plan = _plan(state["options"])
            state.update(status="Vista previa", summary=_summary(plan), signature=_signature(plan), error="")
            _store(token, state)
            frappe.db.commit()
            return

        # DB locks release automatically on a killed worker; no hour-long orphan
        # Redis lease. Hold it through checkpoint + financial-record commit.
        checkpoint.lock_creation()
        if "total_blocks" not in state:
            plan = _plan(state["options"])
            if plan["issues"] or _signature(plan) != state["signature"]:
                frappe.throw(_("Los datos o las aplicaciones existentes cambiaron. Genere otra vista previa antes de importar; se conservarán las empresas seleccionadas."))
            source, content = _file(state["options"]["source_file"])
            if file_sha256(content) != plan["file_hash"]:
                frappe.throw(_("El archivo original cambió durante el análisis. Genere otra vista previa."))
            raw = accounting_source_records(source.file_name, content)
            total = checkpoint.prepare_blocks(token, plan, raw)
            state.update(total_blocks=total, completed_blocks=0, created_count=0,
                         original_content_hash=source.content_hash,
                         progress=_("Plan guardado. {0} bloques pendientes; empresas seleccionadas conservadas.").format(total))
        else:
            index = state.get("completed_blocks", 0) + 1
            if index <= state["total_blocks"]:
                plan, completed = checkpoint.read_block(token, index)
                if completed:
                    frappe.throw(_("El avance del bloque no coincide con la carga. Revise el historial antes de continuar."))
                source = readable_file(state["options"]["source_file"])
                if source.content_hash != state.get("original_content_hash"):
                    frappe.throw(_("El archivo original fue modificado. Los bloques guardados se conservan; revise antes de continuar."))
                _exclude_completed_applications(plan)
                created = _create_imports(plan, state["options"])
                checkpoint.finish_block(token, index, created)
                state.update(completed_blocks=index, created_count=state.get("created_count", 0) + len(created),
                             progress=_("Bloques guardados: {0} de {1}. Documentos registrados/vinculados: {2}.").format(
                                 index, state["total_blocks"], state.get("created_count", 0) + len(created)))
        if state.get("completed_blocks", 0) == state["total_blocks"]:
            state.update(status="Completado", error="")
            _store(token, state)
        else:
            state.update(status="En cola", error="")
            _enqueue(token, state)
        # The checkpoint, document rows, evidence links and individual CSVs are
        # atomic. If queue delivery after this commit fails, Resume queues only
        # the next block using this durable cursor and the original choices.
        frappe.db.commit()
    except Exception as exc:
        frappe.db.rollback()
        frappe.log_error(title="Carga masiva contable", message=frappe.get_traceback())
        message = str(exc) if isinstance(exc, (frappe.ValidationError, SourceFileError)) else _(
            "Se interrumpió la carga. Los bloques guardados y las empresas seleccionadas se conservan. Revise el registro de errores y pulse Reanudar carga."
        )
        saved = _state(token, for_update=True)
        # A queue callback can fail AFTER commit. Read the durable cursor instead
        # of saving a stale in-memory copy or undoing another worker's progress.
        if saved["status"] != "Completado" and (not attempt or saved.get("job_id") in {attempt, state.get("job_id")}):
            saved.update(status="Error", error=message)
            _store(token, saved)
        frappe.db.commit()
