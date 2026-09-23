"""Guard the package layout expected by Frappe's modules.txt sync."""

import ast
import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "credinomina_reconciliation"


class PackageLayoutTest(unittest.TestCase):
    def test_each_declared_module_is_importable(self):
        for label in (PACKAGE / "modules.txt").read_text(encoding="utf-8").splitlines():
            if not label.strip():
                continue
            slug = label.strip().lower().replace(" ", "_")
            module_path = PACKAGE / slug
            self.assertTrue((module_path / "__init__.py").is_file(), label)
            self.assertIsNotNone(
                importlib.util.find_spec(f"credinomina_reconciliation.{slug}"), label
            )

    def test_standard_documents_use_the_declared_module(self):
        declared = {
            label.strip()
            for label in (PACKAGE / "modules.txt").read_text(encoding="utf-8").splitlines()
            if label.strip()
        }
        module_dir = PACKAGE / "conciliacion_credinomina"
        for folder in ("doctype", "page", "report", "workspace"):
            for path in (module_dir / folder).rglob("*.json"):
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn(document["module"], declared, str(path))

    def test_workspace_buttons_point_to_existing_python_methods(self):
        module_dir = PACKAGE / "conciliacion_credinomina"
        for javascript in module_dir.rglob("*.js"):
            content = javascript.read_text(encoding="utf-8")
            for route in re.findall(r'method:\s*"([^"]+)"', content):
                parts = route.split(".")
                self.assertEqual(parts[0], "credinomina_reconciliation")
                module_file = PACKAGE.joinpath(*parts[1:-1]).with_suffix(".py")
                self.assertTrue(module_file.is_file(), route)
                tree = ast.parse(module_file.read_text(encoding="utf-8"))
                functions = {
                    node.name for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                self.assertIn(parts[-1], functions, route)


if __name__ == "__main__":
    unittest.main()
