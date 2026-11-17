"""scripts/build.py's zip holds what the QGIS plugin repository asks of an upload, in plain Python."""

import configparser
import importlib.util
import pathlib
import re
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("castlegate_build", ROOT / "scripts" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

# The fields the QGIS plugin repository's validator refuses an upload without.
REQUIRED = ("name", "qgisMinimumVersion", "description", "about", "version", "author", "email", "repository")


def _metadata():
    # The repository reads metadata.txt with configparser's default interpolation, so a "%" in it fails the upload.
    parser = configparser.ConfigParser()
    parser.read(ROOT / "server" / "castlegate_server" / "metadata.txt", encoding="utf-8")
    return parser["general"]


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._work = tempfile.TemporaryDirectory()
        _, cls.archive = build.build(cls._work.name)
        with zipfile.ZipFile(cls.archive) as z:
            cls.names = z.namelist()

    @classmethod
    def tearDownClass(cls):
        cls._work.cleanup()

    def test_is_named_for_the_version(self):
        self.assertEqual(self.archive.name, "castlegate_server-%s.zip" % _metadata()["version"])

    def test_holds_one_folder(self):
        self.assertEqual({name.split("/")[0] for name in self.names}, {"castlegate_server"})

    def test_holds_the_licence_and_the_bandit_settings_beside_metadata(self):
        # The repository refuses a zip without LICENSE, and reads .bandit only at the plugin's top level.
        for name in ("metadata.txt", "__init__.py", "LICENSE", ".bandit", "core/__init__.py"):
            self.assertIn("castlegate_server/" + name, self.names)

    def test_holds_no_compiled_python(self):
        self.assertEqual([name for name in self.names if re.search(r"__pycache__|\.pyc$", name)], [])

    def test_metadata_has_every_required_field_and_its_icon(self):
        metadata = _metadata()
        self.assertEqual([field for field in REQUIRED if not metadata.get(field)], [])
        self.assertIn("castlegate_server/" + metadata["icon"], self.names)

    def test_metadata_marks_the_plugin_experimental(self):
        self.assertEqual(_metadata().getboolean("experimental"), True)

    def test_the_status_answer_reports_the_version_in_metadata(self):
        status = (ROOT / "server" / "castlegate_server" / "status.py").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^VERSION = "([^"]+)"', status, re.M).group(1), _metadata()["version"])


if __name__ == "__main__":
    unittest.main()
