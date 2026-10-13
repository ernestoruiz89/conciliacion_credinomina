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

# Embed the shared dialog in DocType metadata, including list-only sessions.
# No public asset request/build is needed when opening the bulk importer.
doctype_js = {"CN Accounting Import": "public/js/accounting_bulk.js"}
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
