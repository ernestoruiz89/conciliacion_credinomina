"""Round persisted monetary amounts to cents without touching FX rates."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

import frappe

from credinomina_reconciliation.parsers import clean_text, source_key
from credinomina_reconciliation.reconciliation import remittance_fx_basis
from credinomina_reconciliation.rounding import decimal_value, money, money_float


MONEY_DOCTYPES = (
    "CN Collection Row",
    "CN Complementary Item",
    "CN Credit Portfolio Row",
    "CN Credit Portfolio Snapshot",
    "CN Deposit Surplus",
    "CN Employer",
    "CN Reconciliation Exception",
    "CN Reconciliation Movement",
    "CN Reconciliation Period",
    "CN Remittance Allocation",
    "CN Remittance Detail",
    "CN Remittance Target",
    "CN Source Import",
    "CN Source Row",
)

JSON_FIELDS = {
    "CN Collection Row": ("remittance_detail",),
    "CN Remittance Allocation": ("allocation_detail",),
    "CN Remittance Detail": ("matched_targets",),
    "CN Source Row": (
        "application_allocation_detail", "historical_detail",
        "allocation_detail", "rounding_movement_detail",
    ),
}

JSON_MONEY_KEYS = {
    "amount", "amount_usd", "amount_nio", "expected_usd", "expected_nio",
    "deducted_usd", "deducted_nio", "applied_usd", "applied_nio",
    "remitted_usd", "remitted_nio", "importe_usd", "diferencia_usd",
    "signed_amount_usd", "consumed_residual_usd", "absorbed_cash_usd",
    "tolerance_usd", "core_applied_usd", "deposit_usd", "claim_usd",
    "gap_usd", "rounding_adjustment_usd", "fx_variance_usd",
    "complementary_usd", "allocated_usd", "unallocated_usd",
    "justified_surplus_usd", "unclassified_usd",
}


def _set_if_changed(doctype, name, field, value):
    current = frappe.db.get_value(doctype, name, field)
    rounded = money(value)
    if decimal_value(current) != rounded:
        frappe.db.set_value(doctype, name, field, money_float(rounded), update_modified=False)


def _round_json_amounts(value):
    changed = False
    if isinstance(value, list):
        result = []
        for item in value:
            converted, item_changed = _round_json_amounts(item)
            result.append(converted)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in JSON_MONEY_KEYS and isinstance(item, (int, float)) and not isinstance(item, bool):
                rounded = money_float(item)
                result[key] = rounded
                changed = changed or rounded != item
            else:
                converted, item_changed = _round_json_amounts(item)
                result[key] = converted
                changed = changed or item_changed
        return result, changed
    return value, False


def _round_doctype_fields(doctype):
    if not frappe.db.table_exists(doctype):
        return
    meta = frappe.get_meta(doctype)
    money_fields = [
        field.fieldname for field in meta.fields
        if field.fieldtype == "Currency"
        or (field.fieldtype == "Float" and str(field.precision or "") == "2")
    ]
    json_fields = [field for field in JSON_FIELDS.get(doctype, ()) if meta.has_field(field)]
    fields = ["name", *money_fields, *json_fields]
    start, page_size = 0, 2000
    while True:
        rows = frappe.get_all(
            doctype, fields=fields, start=start, limit_page_length=page_size,
        )
        for row in rows:
            updates = {}
            for field in money_fields:
                value = row.get(field)
                if value not in (None, ""):
                    rounded = money(value)
                    if decimal_value(value) != rounded:
                        # Quantize with Decimal first; convert only at the
                        # Frappe database boundary after the cents are fixed.
                        updates[field] = money_float(rounded)
            for field in json_fields:
                raw = row.get(field)
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                converted, changed = _round_json_amounts(parsed)
                if changed:
                    updates[field] = json.dumps(converted, ensure_ascii=False)
            if updates:
                frappe.db.set_value(doctype, row.name, updates, update_modified=False)
        if len(rows) < page_size:
            break
        start += len(rows)


def _rebuild_source_keys():
    doctype = "CN Source Row"
    if not frappe.db.table_exists(doctype):
        return
    fields = ["name", "source_key", "event_type", "event_date", "reference", "loan_number", "currency", "amount"]
    start, page_size = 0, 2000
    while True:
        rows = frappe.get_all(doctype, fields=fields, start=start, limit_page_length=page_size)
        for row in rows:
            key = source_key(
                row.event_type, row.event_date, row.reference, row.loan_number,
                row.currency, money_float(row.amount),
            )
            if key != row.source_key:
                frappe.db.set_value(doctype, row.name, "source_key", key, update_modified=False)
        if len(rows) < page_size:
            break
        start += len(rows)


def _recompute_usd_equivalents():
    """Recalculate derived USD fields from cent-rounded native amounts."""
    if frappe.db.table_exists("CN Remittance Allocation"):
        for row in frappe.get_all(
            "CN Remittance Allocation",
            fields=["name", "deposit_currency", "deposit_amount", "fx_rate", "notes", "support_file"],
            limit_page_length=1000000,
        ):
            if row.deposit_currency == "USD":
                _set_if_changed("CN Remittance Allocation", row.name, "amount_usd", row.deposit_amount)
            elif (
                row.deposit_currency == "NIO" and decimal_value(row.fx_rate) > 0
                and remittance_fx_basis(dict(row))
            ):
                _set_if_changed(
                    "CN Remittance Allocation", row.name, "amount_usd",
                    decimal_value(row.deposit_amount) / decimal_value(row.fx_rate),
                )

    if frappe.db.table_exists("CN Complementary Item"):
        for row in frappe.get_all(
            "CN Complementary Item", fields=["name", "currency", "amount", "fx_rate"],
            limit_page_length=1000000,
        ):
            if row.currency == "USD":
                value = row.amount
            elif row.currency == "NIO" and decimal_value(row.fx_rate) > 0:
                value = decimal_value(row.amount) / decimal_value(row.fx_rate)
            else:
                continue
            _set_if_changed("CN Complementary Item", row.name, "amount_usd", value)

    if frappe.db.table_exists("CN Source Row"):
        for row in frappe.get_all(
            "CN Source Row",
            fields=["name", "amount", "amount_usd", "amount_nio", "manual_fx_rate"],
            filters={"manual_fx_rate": [">", 0]},
            limit_page_length=1000000,
        ):
            if decimal_value(row.amount_nio) <= 0:
                continue
            value = decimal_value(row.amount_nio) / decimal_value(row.manual_fx_rate)
            _set_if_changed("CN Source Row", row.name, "amount", value)
            _set_if_changed("CN Source Row", row.name, "amount_usd", value)

    if frappe.db.table_exists("CN Remittance Detail") and frappe.db.table_exists("CN Remittance Allocation"):
        rates = {
            row.name: decimal_value(row.fx_rate)
            for row in frappe.get_all(
                "CN Remittance Allocation",
                fields=["name", "fx_rate", "notes", "support_file"],
                limit_page_length=1000000,
            )
            if remittance_fx_basis(dict(row))
        }
        for row in frappe.get_all(
            "CN Remittance Detail",
            fields=["name", "parent", "deducted_usd", "deducted_nio"],
            limit_page_length=1000000,
        ):
            if decimal_value(row.deducted_usd) > 0:
                value = row.deducted_usd
            elif decimal_value(row.deducted_nio) > 0 and rates.get(row.parent, 0) > 0:
                value = decimal_value(row.deducted_nio) / rates[row.parent]
            else:
                continue
            _set_if_changed("CN Remittance Detail", row.name, "amount_usd", value)


def _refresh_historical_fingerprints():
    if not frappe.db.table_exists("CN Reconciliation Period") or not frappe.db.table_exists("CN Source Row"):
        return
    periods = frappe.get_all(
        "CN Reconciliation Period",
        filters={"reconciliation_mode": "Historica"},
        fields=["name", "historical_fingerprint"],
        limit_page_length=1000000,
    )
    if not periods:
        return
    period_names = {row.name for row in periods}
    applications = frappe.get_all(
        "CN Source Row",
        filters={
            "event_type": "Aplicacion", "effective": 1,
            "match_status": "Conciliado", "historical_period": ["in", list(period_names)],
        },
        fields=["name", "amount", "historical_period", "historical_detail"],
        order_by="name asc", limit_page_length=1000000,
    )
    by_period = defaultdict(list)
    for row in applications:
        try:
            details = json.loads(row.historical_detail or "[]")
        except (TypeError, ValueError):
            details = []
        by_period[row.historical_period].append({
            "application_id": row.name,
            "amount_usd": money_float(row.amount),
            "deposits": sorted(
                details,
                key=lambda item: (
                    clean_text(item.get("referencia")),
                    clean_text(item.get("comprobante")),
                    clean_text(item.get("fecha")),
                    float(item.get("importe_usd") or 0),
                ),
            ),
        })
    for period in periods:
        fingerprint = hashlib.sha256(
            json.dumps(
                by_period[period.name], ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint != period.historical_fingerprint:
            frappe.db.set_value(
                "CN Reconciliation Period", period.name,
                "historical_fingerprint", fingerprint, update_modified=False,
            )


def execute():
    for doctype in MONEY_DOCTYPES:
        _round_doctype_fields(doctype)
    _recompute_usd_equivalents()
    _rebuild_source_keys()
    _refresh_historical_fingerprints()
