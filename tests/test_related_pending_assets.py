"""The pending modal must travel with refreshed form metadata, not a cached URL."""
from pathlib import Path

import pytest

from credinomina_reconciliation import hooks


@pytest.mark.parametrize("doctype", [
    "CN Accounting Import", "CN Remittance Allocation", "CN Complementary Item", "CN Client",
])
def test_pending_modal_embedded_in_all_entry_points(doctype):
    scripts = hooks.doctype_js[doctype]
    if isinstance(scripts, str):
        scripts = [scripts]
    assert scripts.count("public/js/related_pending_modal.js") == 1
    root = Path(hooks.__file__).parent
    for script in scripts:
        assert (root / script).is_file()
    controller = doctype.lower().replace(" ", "_")
    source = (root / "conciliacion_credinomina" / "doctype" / controller / f"{controller}.js").read_text(encoding="utf-8")
    assert "window.credinominaShowRelatedPending(" in source
    assert "related_pending_modal.js" not in source
