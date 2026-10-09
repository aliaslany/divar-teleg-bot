"""Configuration parsing in fresh interpreters with synthetic credentials."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRINT_CONFIG = (
    "import config, json; "
    "print(json.dumps([config.SEARCH_CITY_IDS, config.SEARCH_CATEGORY]))"
)


class ConfigTests(unittest.TestCase):
    def run_config(self, overrides=None, missing=()):
        env = {
            "BOT_TOKEN": "synthetic-token",
            "BOT_CHATID": "synthetic-chat",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env.update(overrides or {})
        for name in missing:
            env.pop(name, None)
        return subprocess.run(
            [sys.executable, "-c", _PRINT_CONFIG],
            cwd=_REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_search(self, overrides, cities, category):
        result = self.run_config(overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [cities, category])

    def test_absent_optional_variables_use_documented_defaults(self):
        self.assert_search({}, ["897"], "real-estate")

    def test_blank_optional_variables_use_documented_defaults(self):
        for value in ("", " ", "\t\n"):
            with self.subTest(value=value):
                self.assert_search(
                    {"SEARCH_CITY_IDS": value, "SEARCH_CATEGORY": value},
                    ["897"],
                    "real-estate",
                )

    def test_empty_comma_separated_city_list_uses_default(self):
        self.assert_search({"SEARCH_CITY_IDS": " , ,\t,"}, ["897"], "real-estate")

    def test_city_list_and_category_trim_surrounding_whitespace(self):
        self.assert_search(
            {
                "SEARCH_CITY_IDS": " 823, , 1996,,1999 ",
                "SEARCH_CATEGORY": "  apartments  ",
            },
            ["823", "1996", "1999"],
            "apartments",
        )

    def test_missing_required_variables_name_the_requirement(self):
        for name in ("BOT_TOKEN", "BOT_CHATID"):
            with self.subTest(name=name):
                result = self.run_config(missing=(name,))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name + " must be set to a non-empty value", result.stderr)
                self.assertNotIn("synthetic-token", result.stdout + result.stderr)
                self.assertNotIn("synthetic-chat", result.stdout + result.stderr)

    def test_blank_required_variables_fail_without_values_in_diagnostics(self):
        for name in ("BOT_TOKEN", "BOT_CHATID"):
            for value in ("", " \t\n"):
                with self.subTest(name=name, value=value):
                    result = self.run_config({name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        name + " must be set to a non-empty value", result.stderr
                    )
                    self.assertNotIn("synthetic-token", result.stdout + result.stderr)
                    self.assertNotIn("synthetic-chat", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
