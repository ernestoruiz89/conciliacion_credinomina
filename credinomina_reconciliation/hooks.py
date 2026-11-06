app_name = "credinomina_reconciliation"
app_title = "Conciliación Credinómina"
app_publisher = "MIDESA"
app_description = "Standalone payroll-loan reconciliation for Frappe Framework"
app_email = ""
app_license = "MIT"
app_logo_url = "/assets/credinomina_reconciliation/images/credinomina-logo.svg"
app_home = "/app/conciliacion-credinomina"

# Native app registration, shared by Frappe's apps screen and v16 Desk.
add_to_apps_screen = [{
    "name": app_name,
    "title": app_title,
    "logo": app_logo_url,
    "route": app_home,
    "has_permission": "credinomina_reconciliation.desktop.check_app_permission",
    "sequence_id": 20,
}]

required_apps = []

before_install = "credinomina_reconciliation.install.before_install"
after_install = "credinomina_reconciliation.install.after_install"

# Embed shared dialogs in DocType metadata, including list-only sessions.
# Standalone public assets can remain cached after a deployment.
doctype_js = {
    "CN Accounting Import": ["public/js/accounting_bulk.js", "public/js/difference_documentation.js", "public/js/row_link_details.js", "public/js/document_reopening.js", "public/js/related_pending_modal.js"],
    "CN Remittance Allocation": ["public/js/difference_documentation.js", "public/js/row_link_details.js", "public/js/document_reopening.js", "public/js/related_pending_modal.js"],
    "CN Reconciliation Period": "public/js/difference_documentation.js",
    "CN Complementary Item": ["public/js/difference_documentation.js", "public/js/document_reopening.js", "public/js/related_pending_modal.js"],
    "CN Client": "public/js/related_pending_modal.js",
}
doctype_list_js = {"CN Accounting Import": "public/js/accounting_bulk.js"}

MONEY_DOCTYPES = (
    "CN Collection Row",
    "CN Complementary Item",
    "CN Credit Portfolio Row",
    "CN Credit Portfolio Snapshot",
    "CN Employer",
    "CN Reconciliation Exception",
    "CN Reconciliation Period",
    "CN Remittance Allocation",
    "CN Remittance Detail",
    "CN Remittance Target",
    "CN Accounting Import",
    "CN Source Row",
)

doc_events = {
    doctype: {
        "before_save": "credinomina_reconciliation.rounding.round_document_money",
    }
    for doctype in MONEY_DOCTYPES
}
doc_events["File"] = {
    "on_trash": "credinomina_reconciliation.import_file_guard.guard_import_file_deletion",
}
doc_events["CN Complementary Item"]["on_change"] = "credinomina_reconciliation.complementary_state.item_changed"
doc_events["CN Remittance Allocation"]["on_change"] = "credinomina_reconciliation.complementary_state.deposit_changed"
doc_events["Version"] = {"after_insert": "credinomina_reconciliation.reconciliation_trace.capture_version"}
doc_events["CN Remittance Allocation"]["on_cancel"] = "credinomina_reconciliation.reconciliation_trace.capture_deposit_cancel"
doc_events["CN Complementary Item"]["on_change"] = [
    "credinomina_reconciliation.complementary_state.item_changed",
    "credinomina_reconciliation.reconciliation_trace.capture_item_journals",
]
for event in ("on_submit", "on_cancel"):
    doc_events["CN Complementary Item"][event] = "credinomina_reconciliation.reconciliation_trace.capture_item_confirmation"
