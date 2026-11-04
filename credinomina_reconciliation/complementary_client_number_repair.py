"""Repair names stored as client numbers without replaying financial events."""
import json
from collections import defaultdict

import frappe

from credinomina_reconciliation.client_identity import matching_name, name_key
from credinomina_reconciliation.client_registry import load_client_index
from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, clean_text
from credinomina_reconciliation.paying_employers import choose_detail_client
from credinomina_reconciliation.report_records import _pages


class ReviewRequired(ValueError):
    pass


def is_candidate(item):
    value = clean_text(item.get("client_number"))
    if not value or not any(char.isalpha() for char in value):
        return False
    return (len(value.split()) > 1 or any(name_key(value) == name_key(item.get(field))
            for field in ("client_name", "source_client_name")))


def candidate_rows():
    return [row for row in _pages(frappe.get_all, "CN Complementary Item",
        filters={"client_number": ["is", "set"]}, fields=["name", "employer", "client_number",
            "client_name", "source_client_name", "loan_number", "credit_client"]) if is_candidate(row)]


def plan(item, clients, evidence):
    if not is_candidate(item):
        return None
    old = clean_text(item.get("client_number"))
    # Alphanumeric identifiers remain valid identifiers even when they look
    # like a person's name. Do not replace a number registered to any client.
    if any(canonical_identifier(client.get("client_number")) == canonical_identifier(old) for client in clients):
        return None
    company, loan = clean_text(item.get("employer")), clean_text(item.get("loan_number"))
    if not company or not loan:
        raise ReviewRequired("Falta empresa o crédito; no se puede verificar al propietario.")
    if company not in getattr(evidence, "loan_employers", {}).get(canonical_credit_number(loan), set()):
        raise ReviewRequired("No hay evidencia del crédito en aplicaciones o cartera de esa empresa.")
    probe = dict(client_name=old, loan_number=loan, employer=company)
    client, reason = choose_detail_client(probe, clients, company, [company], evidence)
    if not client:
        raise ReviewRequired(reason)
    if item.get("credit_client") and item.get("credit_client") != client["name"]:
        raise ReviewRequired("El beneficiario registrado no coincide con el propietario del crédito.")
    for field in ("client_name", "source_client_name"):
        value = clean_text(item.get(field))
        if value and value.casefold() not in {"0", "sin identificar", "n/a", "na"} and not matching_name(value, client):
            raise ReviewRequired("El nombre registrado contradice al propietario verificado del crédito.")
    number = clean_text(client.get("client_number"))
    if not number or canonical_identifier(number) == canonical_identifier(old):
        raise ReviewRequired("El catálogo no contiene un número de cliente diferente y verificable.")
    return dict(client=client["name"], client_number=number, previous=old,
        employer=company, loan_number=loan, client_name=client.get("client_name"))


def _assert_linked_clients(item, proposal):
    # A historical manual destination may belong to another loan of the same
    # person. Check the beneficiary, never reassign or rewrite those targets.
    names = set(frappe.get_all("CN Remittance Target", filters={"complementary_item": item.name,
        "parenttype": "CN Remittance Allocation", "docstatus": ["<", 2]}, pluck="parent", limit_page_length=0))
    def ledger(raw):
        try:
            result = json.loads(raw or "[]") if isinstance(raw, str) else raw or []
            if not isinstance(result, list) or any(not isinstance(entry, dict) for entry in result):
                raise ValueError()
            return result
        except (TypeError, ValueError):
            raise ReviewRequired("Un depósito potencialmente vinculado tiene un diario ilegible.")
    for deposit in frappe.get_all("CN Remittance Allocation", filters={"docstatus": 1,
            "allocation_detail": ["like", "%" + item.name + "%"]}, fields=["name", "allocation_detail"], limit_page_length=0):
        if any(entry.get("partida") == item.name for entry in ledger(deposit.allocation_detail)):
            names.add(deposit.name)
    number = canonical_identifier(proposal["client_number"])
    for name in sorted(names):
        deposit = frappe.get_doc("CN Remittance Allocation", name)
        if deposit.docstatus == 2:
            continue
        rows = {row.name: row for row in deposit.get("detail_rows") or []}
        row_names = {target.get("detail_row") for target in deposit.get("targets") or []
                 if target.get("complementary_item") == item.name and target.get("detail_row")}
        row_names.update(entry.get("fila_detalle") for entry in ledger(deposit.get("allocation_detail"))
                     if entry.get("partida") == item.name and entry.get("fila_detalle"))
        for name in row_names:
            row = rows.get(name)
            if not row:
                raise ReviewRequired("Un vínculo conserva una fila de detalle que ya no existe.")
            if row.get("employer") and row.get("employer") != proposal["employer"]:
                raise ReviewRequired("Un detalle vinculado pertenece a otra empresa; se conserva para revisión.")
            if ((row.get("client") and row.get("client") != proposal["client"])
                    or (row.get("client_number") and canonical_identifier(row.get("client_number")) != number)):
                raise ReviewRequired("Un depósito vinculado identifica a otro cliente; se conserva para revisión.")


def _record_version(item, proposal):
    data = dict(changed=[["client_number", item.client_number, proposal["client_number"]]],
        added=[], removed=[], row_changed=[], cn_client_number_repair=dict(
            client=proposal["client"], employer=proposal["employer"], loan_number=proposal["loan_number"],
            basis="Propietario único verificado por crédito, empresa y nombre exacto o alias registrado; sin recalcular conciliaciones."))
    frappe.get_doc(dict(doctype="Version", ref_doctype="CN Complementary Item", docname=item.name,
        data=json.dumps(data, ensure_ascii=False))).insert(ignore_permissions=True)


def _repair(name, company, clients, evidence, dry_run):
    item = frappe.get_doc("CN Complementary Item", name, for_update=not dry_run)
    if clean_text(item.get("employer")) != company:
        raise ReviewRequired("La empresa de la partida cambió durante el proceso.")
    proposal = plan(item, clients, evidence)
    if not proposal:
        return False
    registered = frappe.get_doc("CN Client", proposal["client"], for_update=not dry_run)
    if canonical_identifier(registered.client_number) != canonical_identifier(proposal["client_number"]):
        raise ReviewRequired("El número del cliente cambió durante la verificación.")
    _assert_linked_clients(item, proposal)
    if dry_run:
        return True
    # Only the descriptive identifier changes. No save/submit hooks, financial
    # ledgers, statuses, dates, references or reconciliation traces are replayed.
    frappe.db.set_value(item.doctype, item.name, "client_number", proposal["client_number"], update_modified=False)
    _record_version(item, proposal)
    frappe.clear_document_cache(item.doctype, item.name)
    return True


def run(dry_run=False):
    candidates = candidate_rows()
    result = dict(repaired=[], would_repair=[], review={})
    if not candidates:
        return result
    companies = sorted({clean_text(item.get("employer")) for item in candidates if item.get("loan_number")} - {""})
    if not dry_run and companies:
        from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
        from credinomina_reconciliation.paying_employers import reconciliation_companies, load_payer_map
        from credinomina_reconciliation.complementary_distribution import shared_company_pools
        mapping, pools = load_payer_map(), shared_company_pools()
        lock_cash_pool(sorted({company for employer in companies
            for company in reconciliation_companies(employer, mapping, pools)}))
    clients = load_client_index()
    grouped = defaultdict(list)
    for item in candidates:
        grouped[clean_text(item.get("employer"))].append(item)
    for company, items in sorted(grouped.items()):
        probes = [dict(client_name=item.client_number, loan_number=item.loan_number, employer=company)
                  for item in items if item.get("loan_number") and company]
        evidence = load_detail_loan_clients(probes, clients, [company]) if probes else {}
        for item in items:
            if not dry_run:
                frappe.db.savepoint("repair_complementary_client_number")
            try:
                if _repair(item.name, company, clients, evidence, dry_run):
                    result["would_repair" if dry_run else "repaired"].append(item.name)
            except Exception as error:
                if not dry_run:
                    frappe.db.rollback(save_point="repair_complementary_client_number")
                if not isinstance(error, ReviewRequired):
                    raise
                result["review"][item.name] = str(error)
                frappe.logger(__name__).warning("Partida %s conservada para revisión: %s", item.name, error)
    return result
