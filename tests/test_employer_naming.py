import configparser
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from credinomina_reconciliation.employer_naming import (
    build_employer_rename_plan,
    employer_alias_index,
)


class EmployerNamingTest(unittest.TestCase):
    def test_doctype_uses_name_and_patch_runs_after_schema_sync(self):
        root = Path(__file__).resolve().parents[1]
        doctype_path = (
            root / "credinomina_reconciliation" / "conciliacion_credinomina"
            / "doctype" / "cn_employer" / "cn_employer.json"
        )
        doctype = json.loads(doctype_path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        self.assertEqual("field:employer_name", doctype["autoname"])
        self.assertEqual(1, fields["employer_code"]["unique"])
        self.assertEqual("employer_code", doctype["search_fields"])
        patches = configparser.ConfigParser(allow_no_value=True, delimiters="\n")
        patches.optionxform = str
        patches.read(root / "credinomina_reconciliation" / "patches.txt")
        self.assertEqual(
            [
                "credinomina_reconciliation.patches.v1_0.move_remittance_fx_evidence_to_notes",
            ],
            list(patches["pre_model_sync"]),
        )
        self.assertEqual(
            [
                "credinomina_reconciliation.patches.v1_0.rename_cn_employers_by_name",
                "credinomina_reconciliation.patches.v1_0.assign_client_employers",
                "credinomina_reconciliation.patches.v1_0.backfill_clients",
                "credinomina_reconciliation.patches.v1_0.add_aging_workspace_link",
                "credinomina_reconciliation.patches.v1_0.ensure_client_workspace_link",
            ],
            list(patches["post_model_sync"]),
        )

    def test_patch_preserves_codes_when_renaming_existing_records(self):
        root = Path(__file__).resolve().parents[1]
        patch_path = (
            root / "credinomina_reconciliation" / "patches" / "v1_0"
            / "rename_cn_employers_by_name.py"
        )

        class Row(dict):
            __getattr__ = dict.__getitem__

        records = {
            "CODE-A": Row(name="CODE-A", employer_name="CODE-B", employer_code="A"),
            "CODE-B": Row(name="CODE-B", employer_name="Empresa B", employer_code="B"),
        }
        rename_steps = []

        def rename_doc(
            _doctype, old, new, force=False, merge=False, *,
            ignore_if_exists=False, show_alert=True, rebuild_search=True,
        ):
            self.assertTrue(force)
            self.assertFalse(merge)
            self.assertFalse(ignore_if_exists)
            self.assertFalse(show_alert)
            self.assertFalse(rebuild_search)
            item = records.pop(old)
            item["name"] = new
            item["employer_name"] = new  # Frappe's field:employer_name behavior.
            records[new] = item
            rename_steps.append((old, new))

        db = types.SimpleNamespace(
            table_exists=lambda _doctype: True,
            set_value=lambda _doctype, name, field, value, **_kwargs: records[name].__setitem__(field, value),
            get_value=lambda _doctype, name, _fields, **_kwargs: records.get(name),
        )
        frappe = types.ModuleType("frappe")
        frappe.db = db
        frappe.get_all = lambda *_args, **_kwargs: [Row(item) for item in records.values()]
        frappe.get_meta = lambda _doctype: types.SimpleNamespace(autoname="field:employer_name")
        frappe.rename_doc = rename_doc
        frappe.throw = lambda message: (_ for _ in ()).throw(ValueError(message))
        frappe._ = lambda message: message
        model = types.ModuleType("frappe.model")
        naming = types.ModuleType("frappe.model.naming")
        naming.validate_name = lambda _doctype, name: name
        with patch.dict(sys.modules, {
            "frappe": frappe, "frappe.model": model, "frappe.model.naming": naming,
        }):
            spec = importlib.util.spec_from_file_location("_test_employer_patch", patch_path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.execute()

        self.assertEqual({"CODE-B", "Empresa B"}, set(records))
        self.assertEqual("A", records["CODE-B"]["employer_code"])
        self.assertEqual("B", records["Empresa B"]["employer_code"])
        self.assertEqual(4, len(rename_steps))

    def test_two_phase_plan_handles_existing_code_name_cycles(self):
        normalized, renames = build_employer_rename_plan([
            {"name": "CODE-A", "employer_name": "CODE-B"},
            {"name": "CODE-B", "employer_name": "Empresa B"},
        ])
        self.assertEqual({"CODE-A": "CODE-B", "CODE-B": "Empresa B"}, normalized)
        self.assertEqual(2, len(renames))
        temporary_names = {temporary for _old, temporary, _target in renames}
        self.assertEqual(2, len(temporary_names))
        self.assertFalse(temporary_names & {"CODE-A", "CODE-B", "Empresa B"})

    def test_existing_correct_name_needs_no_rename(self):
        normalized, renames = build_employer_rename_plan([
            {"name": "Empresa A", "employer_name": "Empresa A"},
        ])
        self.assertEqual({"Empresa A": "Empresa A"}, normalized)
        self.assertEqual([], renames)

    def test_rejects_empty_or_duplicate_display_names_before_writing(self):
        with self.assertRaisesRegex(ValueError, "no tiene employer_name"):
            build_employer_rename_plan([{"name": "A", "employer_name": " "}])
        with self.assertRaisesRegex(ValueError, "duplicado"):
            build_employer_rename_plan([
                {"name": "A", "employer_name": "Compañía Norte"},
                {"name": "B", "employer_name": "Compania Norte"},
            ])

    def test_code_remains_an_alias_but_cross_company_collision_is_ambiguous(self):
        aliases, ambiguous = employer_alias_index([
            {"name": "Empresa A", "employer_name": "Empresa A", "employer_code": "A-01"},
            {"name": "A-01", "employer_name": "A-01", "employer_code": "B-02"},
        ])
        self.assertEqual("Empresa A", aliases["empresa a"])
        self.assertNotIn("a-01", aliases)
        self.assertIn("a-01", ambiguous)


if __name__ == "__main__":
    unittest.main()
