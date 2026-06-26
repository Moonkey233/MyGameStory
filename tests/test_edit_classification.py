from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from edit_classification import (  # noqa: E402
    GameRow,
    build_edit_records,
    filter_rows,
    parse_category_filter,
    parse_new_category,
    select_rows,
)
from update_steam_library import load_category_choices  # noqa: E402


class EditClassificationTests(unittest.TestCase):
    def test_parse_category_filter_accepts_category_and_special_filters(self) -> None:
        choices = load_category_choices(PROJECT_ROOT)

        self.assertEqual(parse_category_filter("B.10", choices).category, "narrative_adventure")
        self.assertEqual(parse_category_filter("pending", choices).kind, "status")
        self.assertEqual(parse_category_filter("unclassified", choices).kind, "unclassified")
        self.assertEqual(parse_category_filter("all", choices).kind, "all")

    def test_filter_and_select_rows(self) -> None:
        rows = [
            GameRow(1, "steam:1", 1, "One", "action_combat", "confirmed"),
            GameRow(2, "steam:2", 2, "Two", None, "pending"),
            GameRow(3, "steam:3", 3, "Three", "narrative_adventure", "confirmed"),
        ]

        self.assertEqual([row.game_id for row in filter_rows(rows, parse_category_filter("pending", []))], ["steam:2"])
        self.assertEqual([row.game_id for row in select_rows("1,3", rows)], ["steam:1", "steam:3"])
        self.assertEqual([row.game_id for row in select_rows("2", rows)], ["steam:2"])
        self.assertEqual([row.game_id for row in select_rows("steam:3", rows)], ["steam:3"])

    def test_parse_new_category_can_clear_category(self) -> None:
        choices = load_category_choices(PROJECT_ROOT)

        self.assertEqual(parse_new_category("B.10", choices).key, "narrative_adventure")
        self.assertIsNone(parse_new_category("clear", choices))

    def test_build_edit_records_preserves_manual_fields(self) -> None:
        choices = load_category_choices(PROJECT_ROOT)
        choice = parse_new_category("B.10", choices)
        rows = [GameRow(1, "steam:1272840", 1272840, "Dordogne", "action_combat", "confirmed")]

        records = build_edit_records(PROJECT_ROOT, rows, choice, "testtimestamp")

        self.assertEqual(records[0]["game_id"], "steam:1272840")
        self.assertEqual(records[0]["primary_category"], "narrative_adventure")
        self.assertEqual(records[0]["classification_status"], "confirmed")
        self.assertIn("classification_evidence", records[0])


if __name__ == "__main__":
    unittest.main()
