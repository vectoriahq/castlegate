"""The core imports nothing but Python's standard library and its own modules."""

import ast
import pathlib
import sys
import unittest

CORE = pathlib.Path(__file__).resolve().parents[2] / "core"


class Standalone(unittest.TestCase):
    def test_core_imports_only_the_standard_library_and_itself(self):
        found = {}
        for path in sorted(CORE.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue  # relative: one of the core's own modules
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names:
                        found.setdefault(path.name, []).append(name)
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
