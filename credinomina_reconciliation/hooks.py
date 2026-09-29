app_name = "credinomina_reconciliation"
app_title = "Conciliación Credinómina"
app_publisher = "MIDESA"
app_description = "Standalone payroll-loan reconciliation for Frappe Framework"
app_email = ""
app_license = "MIT"

required_apps = []

before_install = "credinomina_reconciliation.install.before_install"

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

doc_events = {
    doctype: {
        "before_save": "credinomina_reconciliation.rounding.round_document_money",
    }
    for doctype in MONEY_DOCTYPES
}
