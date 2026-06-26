from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from update_steam_library import load_category_choices, parse_category_choice  # noqa: E402


class UpdateSteamLibraryTests(unittest.TestCase):
    def test_parse_category_choice_accepts_supported_shortcuts(self) -> None:
        choices = load_category_choices(PROJECT_ROOT)

        self.assertEqual(parse_category_choice("1", choices).key, "action_combat")
        self.assertEqual(parse_category_choice("B.10", choices).key, "narrative_adventure")
        self.assertEqual(parse_category_choice("b10", choices).key, "narrative_adventure")
        self.assertEqual(parse_category_choice("narrative_adventure", choices).key, "narrative_adventure")
        self.assertIsNone(parse_category_choice("", choices))
        self.assertIsNone(parse_category_choice("skip", choices))

    def test_parse_category_choice_rejects_unknown_value(self) -> None:
        choices = load_category_choices(PROJECT_ROOT)

        with self.assertRaises(ValueError):
            parse_category_choice("B.99", choices)


if __name__ == "__main__":
    unittest.main()
