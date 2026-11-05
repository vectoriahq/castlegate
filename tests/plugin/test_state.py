"""Log lines stay one line, in plain Python: castlegate_server/state.py imports QGIS only to write."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "server"))

from castlegate_server.state import one_line  # noqa: E402


class OneLine(unittest.TestCase):
    def test_line_breaks_and_controls_are_escaped(self):
        # Review finding on #3: a line break in a type name wrote a forged audit line.
        self.assertEqual(one_line("a\nb\r\x00c\x85d e f\x7fg\th"), "a\\u000ab\\u000d\\u0000c\\u0085d\\u2028e\\u2029f\\u007fg\\u0009h")

    def test_ordinary_text_is_unchanged(self):
        self.assertEqual(one_line('audit id="x" layer="site notes" ü €'), 'audit id="x" layer="site notes" ü €')

    def test_anything_is_written_as_text(self):
        self.assertEqual(one_line(7), "7")


if __name__ == "__main__":
    unittest.main()
